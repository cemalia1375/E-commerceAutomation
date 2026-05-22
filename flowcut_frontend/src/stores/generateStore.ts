import { create } from 'zustand'
import type { GenerateStep, ChatMessage, Script, VideoScene, VideoSegment, MatchedSegment } from '../types'
import { streamChat, createSession, listSessions } from '../api/chat'
import type { SessionSummary } from '../api/chat'
import {
  uploadReferenceVideo,
  pollReferenceVideo,
} from '../api/referenceVideos'
import { matchMaterials } from '../api/match'

const TENANT_KEY = 'flowcut'

export type DecomposeStatus =
  | 'idle'
  | 'uploading'
  | 'processing'          // SCENE_DECOMPOSE (Gemini + PySceneDetect) queued
  | 'awaiting_classify'   // decompose done, waiting for user to classify segments
  | 'creating_clips'      // clip_create task queued, polling for DECOMPOSED
  | 'done'
  | 'error'

interface GenerateState {
  step: GenerateStep
  messages: ChatMessage[]
  scripts: Script[]
  selectedScriptId: string | null
  isAgentTyping: boolean
  _cancelStream: (() => void) | null

  // Session
  sessionKey: string
  sessions: SessionSummary[]

  // Decompose flow (reference video → scene decompose → clips)
  decomposeStatus: DecomposeStatus
  decomposeError: string | null
  currentRefVideoId: number | null
  sceneData: VideoScene[]

  // Classify modal state
  classifyModalOpen: boolean
  classifyRefVideoId: number | null
  classifySegments: VideoSegment[]

  // Current product (captured from ClassifyModal, used in material match)
  currentProduct: string | null

  // Material match results (step 3)
  matchResults: MatchedSegment[] | null
  matchLoading: boolean
  matchError: string | null

  setStep: (step: GenerateStep) => void
  addMessage: (msg: Omit<ChatMessage, 'id'>) => void
  setScripts: (scripts: Script[]) => void
  selectScript: (id: string) => void
  setAgentTyping: (typing: boolean) => void
  sendUserMessage: (text: string) => void
  startDecomposeFlow: (file: File) => Promise<void>
  openClassifyModal: (refVideoId: number, segments: VideoSegment[]) => void
  closeClassifyModal: () => void
  continueAfterClassify: (product: string) => Promise<void>
  runMaterialMatch: () => Promise<boolean>
  newSession: () => Promise<void>
  fetchSessions: () => Promise<void>
}

let msgCounter = 0
const newId = () => `msg-${++msgCounter}`

// Converts VideoScene[] into a single Script for display in ScriptStep
function scenesToScript(scenes: VideoScene[]): Script {
  const last = scenes[scenes.length - 1]
  return {
    id: 's1',
    name: '拆镜结果',
    hook: scenes[0]?.content.slice(0, 40) ?? '',
    durationSec: last?.endTime ?? 0,
    scenes: scenes.map((s, i) => ({
      startSec: s.startTime,
      endSec: s.endTime,
      label: `场景 ${i + 1}`,
      description: s.content,
      category: s.category,
    })),
  }
}

export const useGenerateStore = create<GenerateState>((set, get) => ({
  step: 1,
  messages: [
    {
      id: newId(),
      role: 'agent',
      type: 'text',
      content: '你好！请上传一条爆款视频（30-40 秒），我来帮你拆解分镜、生成差异化脚本。',
    },
  ],
  scripts: [],
  selectedScriptId: null,
  isAgentTyping: false,
  _cancelStream: null,

  sessionKey: `session-${Date.now()}`,
  sessions: [],

  decomposeStatus: 'idle',
  decomposeError: null,
  currentRefVideoId: null,
  sceneData: [],

  classifyModalOpen: false,
  classifyRefVideoId: null,
  classifySegments: [],

  currentProduct: null,
  matchResults: null,
  matchLoading: false,
  matchError: null,

  setStep: (step) => set({ step }),
  addMessage: (msg) => set((s) => ({ messages: [...s.messages, { ...msg, id: newId() }] })),
  setScripts: (scripts) => set({ scripts }),
  selectScript: (id) => set({ selectedScriptId: id }),
  setAgentTyping: (typing) => set({ isAgentTyping: typing }),

  startDecomposeFlow: async (file: File) => {
    const { addMessage } = get()
    set({ decomposeStatus: 'uploading', decomposeError: null })
    addMessage({ role: 'user', type: 'text', content: `已上传：${file.name}` })

    let decomposeIdx = -1

    try {
      // 1. Upload reference video → fc_reference_video + auto-queue scene_decompose
      const { ref_video_id } = await uploadReferenceVideo(TENANT_KEY, file)
      set({ currentRefVideoId: ref_video_id, decomposeStatus: 'processing' })

      addMessage({
        role: 'agent',
        type: 'progress',
        content: '',
        label: '正在拆解分镜…',
        subLabel: 'Gemini 语义分段 + PySceneDetect 时间对齐',
        done: false,
      })
      decomposeIdx = get().messages.length - 1

      // 2. Poll until AWAITING_CLASSIFICATION (decompose done, clips not yet created)
      const refVideo = await pollReferenceVideo(
        ref_video_id,
        (rv) => rv.status === 'AWAITING_CLASSIFICATION' && (rv.scene_data_json?.length ?? 0) > 0,
      )
      const segments = refVideo.scene_data_json!

      set({
        decomposeStatus: 'awaiting_classify',
        messages: get().messages.map((m, i) =>
          i === decomposeIdx ? { ...m, done: true } : m,
        ),
      })
      addMessage({
        role: 'agent',
        type: 'progress',
        content: '',
        label: '分镜拆解完成，等待分类',
        subLabel: `识别 ${segments.length} 个分镜段，请为每段指定场景角色`,
        done: true,
      })
      addMessage({
        role: 'agent',
        type: 'text',
        content: `拆镜完成，共 ${segments.length} 段。请在弹窗中为每段选择场景角色，并指定产品。`,
      })

      // 3. Open classify modal
      get().openClassifyModal(ref_video_id, segments)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      set({ decomposeStatus: 'error', decomposeError: msg })
      addMessage({ role: 'agent', type: 'text', content: `[错误] ${msg}` })
    }
  },

  openClassifyModal: (refVideoId, segments) =>
    set({ classifyModalOpen: true, classifyRefVideoId: refVideoId, classifySegments: segments }),

  closeClassifyModal: () => set({ classifyModalOpen: false }),

  continueAfterClassify: async (product: string) => {
    const { currentRefVideoId, addMessage } = get()
    if (currentRefVideoId === null) return

    set({ decomposeStatus: 'creating_clips', classifyModalOpen: false, currentProduct: product })
    addMessage({
      role: 'agent',
      type: 'progress',
      content: '',
      label: '正在生成子片段…',
      subLabel: 'FFmpeg 切条 + OSS 上传',
      done: false,
    })
    const clipIdx = get().messages.length - 1

    try {
      const refVideo = await pollReferenceVideo(
        currentRefVideoId,
        (rv) => rv.status === 'DECOMPOSED',
      )
      const sceneData = (refVideo.scene_data_json ?? []).map((s) => ({
        startTime: s.startTime,
        endTime: s.endTime,
        content: s.content,
        category: s.category,
      }))

      const script = scenesToScript(sceneData)
      set({
        decomposeStatus: 'done',
        sceneData,
        scripts: [script],
        selectedScriptId: script.id,
        messages: get().messages.map((m, i) =>
          i === clipIdx ? { ...m, done: true } : m,
        ),
      })
      addMessage({
        role: 'agent',
        type: 'progress',
        content: '',
        label: '子片段生成完成',
        subLabel: `共生成 ${sceneData.length} 条素材`,
        done: true,
      })
      addMessage({
        role: 'agent',
        type: 'text',
        content: `已完成拆镜，共 ${sceneData.length} 段。右侧可查看分镜详情，选择后继续。`,
      })
      get().setStep(2)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      set({ decomposeStatus: 'error', decomposeError: msg })
      addMessage({ role: 'agent', type: 'text', content: `[错误] ${msg}` })
    }
  },

  runMaterialMatch: async () => {
    const { scripts, selectedScriptId, currentProduct, addMessage } = get()
    const sel = scripts.find((s) => s.id === selectedScriptId)
    if (!sel) {
      set({ matchError: '请先选择脚本' })
      return false
    }
    const product = (currentProduct ?? '').trim() || '通用'
    const segments = sel.scenes.map((sc, i) => ({
      index: i,
      description: sc.description,
      duration: Math.max(0, sc.endSec - sc.startSec),
    }))

    set({ matchLoading: true, matchError: null })
    try {
      const results = await matchMaterials({
        tenantKey: TENANT_KEY,
        product,
        segments,
      })
      const matched = results.filter((r) => r.phase1.length > 0).length
      const low = results.filter((r) => r.phase1.length === 0 && r.phase2.length > 0).length
      const missing = results.filter((r) => r.phase1.length === 0 && r.phase2.length === 0).length
      set({ matchResults: results, matchLoading: false })
      addMessage({
        role: 'agent',
        type: 'text',
        content: `匹配完成：已匹配 ${matched} 段，低匹配 ${low} 段，缺失 ${missing} 段，右侧查看详情。`,
      })
      return true
    } catch (err) {
      const msg = err instanceof Error ? err.message : '匹配失败'
      set({ matchLoading: false, matchError: msg })
      addMessage({ role: 'agent', type: 'text', content: `[错误] 素材匹配失败：${msg}` })
      return false
    }
  },

  newSession: async () => {
    const resetState = {
      step: 1 as const,
      messages: [
        {
          id: newId(),
          role: 'agent' as const,
          type: 'text' as const,
          content: '你好！请上传一条爆款视频（30-40 秒），我来帮你拆解分镜、生成差异化脚本。',
        },
      ],
      scripts: [],
      selectedScriptId: null,
      decomposeStatus: 'idle' as const,
      decomposeError: null,
      currentRefVideoId: null,
      sceneData: [],
      classifyModalOpen: false,
      classifyRefVideoId: null,
      classifySegments: [],
      currentProduct: null,
      matchResults: null,
      matchLoading: false,
      matchError: null,
    }
    try {
      const s = await createSession(TENANT_KEY)
      set({ sessionKey: s.session_key, ...resetState })
    } catch {
      // Fallback: reset locally even if API fails
      set({ sessionKey: `session-${Date.now()}`, ...resetState })
    }
    get().fetchSessions()
  },

  fetchSessions: async () => {
    try {
      const list = await listSessions(TENANT_KEY)
      set({ sessions: list })
    } catch {
      // silently fail — session list is cosmetic
    }
  },

  sendUserMessage: (text) => {
    const { _cancelStream, addMessage, sessionKey } = get()

    if (_cancelStream) _cancelStream()

    addMessage({ role: 'user', type: 'text', content: text })

    const agentMsgId = newId()
    set((s) => ({
      isAgentTyping: true,
      messages: [...s.messages, { id: agentMsgId, role: 'agent', type: 'text', content: '' }],
    }))

    const cancel = streamChat({
      tenantKey: TENANT_KEY,
      sessionKey,
      query: text,
      onChunk: (token) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === agentMsgId ? { ...m, content: m.content + token } : m,
          ),
        }))
      },
      onDone: () => set({ isAgentTyping: false, _cancelStream: null }),
      onError: (msg) => {
        set((s) => ({
          isAgentTyping: false,
          _cancelStream: null,
          messages: s.messages.map((m) =>
            m.id === agentMsgId ? { ...m, content: m.content || `[错误] ${msg}` } : m,
          ),
        }))
      },
    })

    set({ _cancelStream: cancel })
  },
}))
