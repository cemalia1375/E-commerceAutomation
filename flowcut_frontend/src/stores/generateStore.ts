import { create } from 'zustand'
import type { GenerateStep, ChatMessage, Script, MatchedSegment } from '../types'
import { streamChat, createSession, listSessions } from '../api/chat'
import type { SessionSummary } from '../api/chat'
import {
  uploadReferenceVideo,
  pollReferenceVideo,
} from '../api/referenceVideos'
import { scriptApi } from '../api/script'
import { matchMaterials } from '../api/match'

const TENANT_KEY = 'flowcut'

export type DecomposeStatus =
  | 'idle'
  | 'uploading'
  | 'processing'
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

  // Decompose flow (reference video → scene decompose → script)
  decomposeStatus: DecomposeStatus
  decomposeError: string | null
  currentRefVideoId: number | null
  currentScriptId: number | null

  // Current product (used in material match; updateable via setProduct)
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
  setProduct: (product: string | null) => Promise<void>
  runMaterialMatch: () => Promise<boolean>
  newSession: () => Promise<void>
  fetchSessions: () => Promise<void>
}

let msgCounter = 0
const newId = () => `msg-${++msgCounter}`

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
  currentScriptId: null,

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
      const { ref_video_id } = await uploadReferenceVideo(TENANT_KEY, file)
      set({ currentRefVideoId: ref_video_id, decomposeStatus: 'processing' })

      addMessage({
        role: 'agent',
        type: 'progress',
        content: '',
        label: '正在拆解分镜并提取口播…',
        subLabel: 'Gemini 视觉分段 + ASR 词级切片',
        done: false,
      })
      decomposeIdx = get().messages.length - 1

      const refVideo = await pollReferenceVideo(
        ref_video_id,
        (rv) => rv.status === 'READY' && rv.script_id !== null,
      )
      if (refVideo.script_id === null) {
        throw new Error('拆镜完成但未生成脚本')
      }

      const script = await scriptApi.get(refVideo.script_id)

      const localScript: Script = {
        id: String(script.id),
        name: '拆镜结果',
        hook: script.segments[0]?.visual?.slice(0, 40) ?? '',
        durationSec: script.segments[script.segments.length - 1]?.end_time ?? 0,
        scenes: script.segments.map((seg, i) => ({
          startSec: seg.start_time,
          endSec: seg.end_time,
          label: `场景 ${i + 1}`,
          description: seg.visual,
          category:
            (seg.category as '真人口播' | '产品展示' | undefined) ?? '产品展示',
          copy: seg.copy,
        })),
      }

      set({
        decomposeStatus: 'done',
        currentScriptId: script.id,
        currentProduct: script.product ?? null,
        scripts: [localScript],
        selectedScriptId: localScript.id,
        messages: get().messages.map((m, i) =>
          i === decomposeIdx ? { ...m, done: true } : m,
        ),
      })
      addMessage({
        role: 'agent',
        type: 'progress',
        content: '',
        label: '拆镜完成',
        subLabel: `共 ${localScript.scenes.length} 段，包含口播文案`,
        done: true,
      })
      addMessage({
        role: 'agent',
        type: 'text',
        content: `已完成拆镜，共 ${localScript.scenes.length} 段。右侧可查看每段的视觉描述和口播文案。下一步：选择产品并匹配素材。`,
      })
      get().setStep(2)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误'
      set({ decomposeStatus: 'error', decomposeError: msg })
      addMessage({ role: 'agent', type: 'text', content: `[错误] ${msg}` })
    }
  },

  setProduct: async (product) => {
    const { currentScriptId } = get()
    const normalized = product?.trim() || null
    if (currentScriptId === null) {
      set({ currentProduct: normalized })
      return
    }
    await scriptApi.updateProduct(currentScriptId, normalized)
    set({ currentProduct: normalized })
  },

  runMaterialMatch: async () => {
    const { scripts, selectedScriptId, currentProduct, addMessage } = get()
    const sel = scripts.find((s) => s.id === selectedScriptId)
    if (!sel) {
      set({ matchError: '请先选择脚本' })
      return false
    }
    const product = (currentProduct ?? '').trim()
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
      currentScriptId: null,
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
