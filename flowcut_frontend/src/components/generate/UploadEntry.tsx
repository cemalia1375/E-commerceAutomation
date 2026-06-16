import { useEffect, useMemo, useState } from 'react'
import {
  Card,
  Modal,
  Upload,
  Button,
  Input,
  InputNumber,
  Form,
  message,
  Spin,
  Radio,
  Select,
} from 'antd'
import type { UploadFile, RcFile } from 'antd/es/upload/interface'
import { useNavigate } from 'react-router-dom'
import { uploadReferenceVideo } from '../../api/referenceVideos'
import {
  listHighlightAssets,
  runHighlightAssetBatch,
  uploadHighlightAsset,
} from '../../api/highlightAssets'
import { scriptApi } from '../../api/script'
import { useCreativeStore } from '../../stores/creativeStore'
import { useAuthStore } from '../../stores/authStore'
import type { HighlightAsset } from '../../types'
import type { ScriptSegment } from '../../types/script'
import ExistingScriptsModal from './ExistingScriptsModal'

interface ParsedSegment {
  visual: string
  copy: string
}

function parseSegmentsText(text: string): ParsedSegment[] {
  const lines = text
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l.length > 0)
  const segs: ParsedSegment[] = []
  for (const line of lines) {
    const parts = line.split('|').map((p) => p.trim())
    if (parts.length < 2) {
      throw new Error(`格式错误，每行需要 "visual | copy"：${line}`)
    }
    segs.push({ visual: parts[0], copy: parts.slice(1).join(' | ') })
  }
  if (segs.length === 0) {
    throw new Error('至少输入一段内容')
  }
  return segs
}

function toSegmentPayload(parsed: ParsedSegment[]): Partial<ScriptSegment>[] {
  return parsed.map((s, idx) => ({
    idx,
    start_time: 0,
    end_time: 0,
    visual: s.visual,
    copy: s.copy,
  }))
}

export default function UploadEntry() {
  const TENANT_KEY = useAuthStore((s) => s.user?.tenantKey) ?? 'flowcut'
  const navigate = useNavigate()
  const setCreativeSubTab = useCreativeStore((s) => s.setSubTab)

  const [videoFile, setVideoFile] = useState<UploadFile | null>(null)
  const [highlightFile, setHighlightFile] = useState<UploadFile | null>(null)
  const [highlightBatchFiles, setHighlightBatchFiles] = useState<UploadFile[]>([])
  const [connectorFile, setConnectorFile] = useState<UploadFile | null>(null)
  const [product, setProduct] = useState('')
  const [highlightProduct, setHighlightProduct] = useState('')
  const [highlightEntryMode, setHighlightEntryMode] = useState<
    'single' | 'local_batch' | 'library_batch'
  >('single')
  const [batchDramaName, setBatchDramaName] = useState('')
  const [batchEpisodeStart, setBatchEpisodeStart] = useState<number | null>(1)
  const [libraryDramaName, setLibraryDramaName] = useState<string | undefined>()
  const [batchConnectorAssetId, setBatchConnectorAssetId] = useState<number | undefined>()
  const [highlightContinuation, setHighlightContinuation] = useState<
    'original' | 'digital_human'
  >('original')
  const [episodeAssets, setEpisodeAssets] = useState<HighlightAsset[]>([])
  const [connectorAssets, setConnectorAssets] = useState<HighlightAsset[]>([])
  const [videoUploading, setVideoUploading] = useState(false)
  const [highlightUploading, setHighlightUploading] = useState(false)

  const [scriptModalOpen, setScriptModalOpen] = useState(false)
  const [segmentsText, setSegmentsText] = useState('')
  const [scriptCreating, setScriptCreating] = useState(false)

  const [existingOpen, setExistingOpen] = useState(false)

  const refreshHighlightAssets = async () => {
    const [episodes, connectors] = await Promise.all([
      listHighlightAssets(TENANT_KEY, { assetType: 'episode_source' }),
      listHighlightAssets(TENANT_KEY, { assetType: 'digital_human_connector' }),
    ])
    setEpisodeAssets(episodes)
    setConnectorAssets(connectors)
  }

  useEffect(() => {
    refreshHighlightAssets().catch(() => {
      // 入口页不因资产库加载失败而阻塞单视频上传。
    })
  }, [])

  const dramaOptions = useMemo(() => {
    const names = Array.from(
      new Set(episodeAssets.map((asset) => asset.dramaName).filter(Boolean) as string[]),
    ).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))
    return names.map((name) => ({
      label: `${name}（${episodeAssets.filter((asset) => asset.dramaName === name).length}）`,
      value: name,
    }))
  }, [episodeAssets])

  const connectorOptions = useMemo(
    () =>
      connectorAssets.map((asset) => ({
        label: `${asset.name}${asset.connectorRole ? ` / ${asset.connectorRole}` : ''}`,
        value: asset.id,
      })),
    [connectorAssets],
  )

  const handleVideoUpload = async (
    workflowType: 'reference_video' | 'highlight_extract',
  ) => {
    const selectedFile = workflowType === 'highlight_extract' ? highlightFile : videoFile
    const selectedProduct = workflowType === 'highlight_extract' ? highlightProduct : product

    if (!selectedFile) {
      message.warning('请先选择视频文件')
      return
    }
    if (workflowType === 'highlight_extract' && highlightContinuation === 'digital_human' && !connectorFile) {
      message.warning('请先选择数字人衔接视频')
      return
    }
    const raw = selectedFile.originFileObj as File | undefined
    if (!raw) {
      message.error('文件读取失败')
      return
    }
    const setUploading = workflowType === 'highlight_extract'
      ? setHighlightUploading
      : setVideoUploading
    setUploading(true)
    try {
      let connectorRefVideoId: number | undefined
      if (workflowType === 'highlight_extract' && highlightContinuation === 'digital_human') {
        const connectorRaw = connectorFile?.originFileObj as File | undefined
        if (!connectorRaw) {
          throw new Error('数字人衔接视频读取失败')
        }
        const connectorResp = await uploadReferenceVideo(
          TENANT_KEY,
          connectorRaw,
          undefined,
          undefined,
          'pending',
        )
        connectorRefVideoId = connectorResp.ref_video_id
      }

      const resp = await uploadReferenceVideo(
        TENANT_KEY,
        raw,
        selectedProduct.trim() || undefined,
        undefined,
        workflowType,
        workflowType === 'highlight_extract'
          ? {
              continuationType: highlightContinuation,
              connectorRefVideoId,
            }
          : undefined,
      )
      if (!resp.script_id) {
        throw new Error('后端未返回 script_id')
      }
      message.success('上传成功，跳转到工作区')
      const query = workflowType === 'highlight_extract'
        ? '?mode=highlight&tab=highlight'
        : ''
      navigate(`/workspace/${resp.script_id}${query}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(`上传失败：${msg}`)
    } finally {
      setUploading(false)
    }
  }

  const runBatchByDrama = async (dramaName: string) => {
    const trimmedDrama = dramaName.trim()
    if (!trimmedDrama) {
      message.warning('请填写或选择 AI 漫剧名称')
      return
    }
    if (highlightContinuation === 'digital_human' && !batchConnectorAssetId) {
      message.warning('请选择一个数字人视频')
      return
    }
    const resp = await runHighlightAssetBatch(TENANT_KEY, {
      dramaName: trimmedDrama,
      mode: highlightContinuation === 'digital_human'
        ? 'highlight_digital_human'
        : 'highlight_original',
      connectorAssetId: highlightContinuation === 'digital_human'
        ? batchConnectorAssetId
        : undefined,
    })
    message.success(`已创建 ${resp.createdCount} 条高光任务`)
    setCreativeSubTab('highlight')
    navigate('/creative')
  }

  const handleLocalBatchHighlight = async () => {
    const drama = batchDramaName.trim()
    if (!drama) {
      message.warning('请填写 AI 漫剧名称')
      return
    }
    if (highlightBatchFiles.length === 0) {
      message.warning('请先选择本地视频')
      return
    }
    setHighlightUploading(true)
    try {
      const startNo = batchEpisodeStart ?? 1
      for (const [idx, item] of highlightBatchFiles.entries()) {
        const raw = item.originFileObj as File | undefined
        if (!raw) continue
        await uploadHighlightAsset(TENANT_KEY, raw, {
          assetType: 'episode_source',
          dramaName: drama,
          episodeNo: startNo + idx,
        })
      }
      await refreshHighlightAssets()
      await runBatchByDrama(drama)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '批量上传或创建任务失败'
      message.error(msg)
    } finally {
      setHighlightUploading(false)
    }
  }

  const handleLibraryBatchHighlight = async () => {
    if (!libraryDramaName) {
      message.warning('请选择素材库中的 AI 漫剧')
      return
    }
    setHighlightUploading(true)
    try {
      await runBatchByDrama(libraryDramaName)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '创建批量任务失败'
      message.error(msg)
    } finally {
      setHighlightUploading(false)
    }
  }

  const handleScriptSubmit = async () => {
    let parsed: ParsedSegment[]
    try {
      parsed = parseSegmentsText(segmentsText)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '格式错误'
      message.error(msg)
      return
    }
    setScriptCreating(true)
    try {
      const resp = await scriptApi.upload(TENANT_KEY, toSegmentPayload(parsed))
      message.success('脚本创建成功')
      setScriptModalOpen(false)
      navigate(`/workspace/${resp.script_id}`)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '创建失败'
      message.error(`创建失败：${msg}`)
    } finally {
      setScriptCreating(false)
    }
  }

  return (
    <div
      style={{
        flex: 1,
        padding: 32,
        overflow: 'auto',
        background: '#f8fafc',
      }}
    >
      <div
        style={{
          maxWidth: 960,
          margin: '0 auto',
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 20,
        }}
      >
        <Card title="上传爆款视频" hoverable>
          <Spin spinning={videoUploading}>
            <Form layout="vertical">
              <Form.Item label="视频文件">
                <Upload
                  beforeUpload={(file: RcFile) => {
                    setVideoFile({
                      uid: file.uid,
                      name: file.name,
                      originFileObj: file,
                      size: file.size,
                      type: file.type,
                    })
                    return false
                  }}
                  onRemove={() => setVideoFile(null)}
                  fileList={videoFile ? [videoFile] : []}
                  maxCount={1}
                  accept="video/*"
                >
                  <Button>选择视频</Button>
                </Upload>
              </Form.Item>
              <Form.Item label="产品（可选）">
                <Input
                  value={product}
                  onChange={(e) => setProduct(e.target.value)}
                  placeholder="留空则用「通用」"
                />
              </Form.Item>
              <Button
                type="primary"
                block
                onClick={() => handleVideoUpload('reference_video')}
                disabled={!videoFile}
                loading={videoUploading}
              >
                上传并拆镜
              </Button>
            </Form>
          </Spin>
        </Card>

        <Card title="上传提取高光视频" hoverable>
          <Spin spinning={highlightUploading}>
            <Form layout="vertical">
              <Form.Item label="提取方式">
                <Radio.Group
                  value={highlightEntryMode}
                  onChange={(e) => setHighlightEntryMode(e.target.value)}
                >
                  <Radio.Button value="single">单个视频</Radio.Button>
                  <Radio.Button value="local_batch">批量本地</Radio.Button>
                  <Radio.Button value="library_batch">从素材库</Radio.Button>
                </Radio.Group>
              </Form.Item>

              {highlightEntryMode === 'single' && (
                <>
                  <Form.Item label="视频文件">
                    <Upload
                      beforeUpload={(file: RcFile) => {
                        setHighlightFile({
                          uid: file.uid,
                          name: file.name,
                          originFileObj: file,
                          size: file.size,
                          type: file.type,
                        })
                        return false
                      }}
                      onRemove={() => setHighlightFile(null)}
                      fileList={highlightFile ? [highlightFile] : []}
                      maxCount={1}
                      accept="video/*"
                    >
                      <Button>选择视频</Button>
                    </Upload>
                  </Form.Item>
                  <Form.Item label="产品（可选）">
                    <Input
                      value={highlightProduct}
                      onChange={(e) => setHighlightProduct(e.target.value)}
                      placeholder="留空则用「通用」"
                    />
                  </Form.Item>
                </>
              )}

              {highlightEntryMode === 'local_batch' && (
                <>
                  <Form.Item label="AI 漫剧名称">
                    <Input
                      value={batchDramaName}
                      onChange={(e) => setBatchDramaName(e.target.value)}
                      placeholder="例如：西瓜地风波"
                    />
                  </Form.Item>
                  <Form.Item label="起始集数">
                    <InputNumber
                      min={1}
                      value={batchEpisodeStart}
                      onChange={(v) => setBatchEpisodeStart(v ?? 1)}
                      style={{ width: '100%' }}
                    />
                  </Form.Item>
                  <Form.Item label="本地视频文件">
                    <Upload
                      beforeUpload={(file: RcFile) => {
                        setHighlightBatchFiles((prev) => [
                          ...prev,
                          {
                            uid: file.uid,
                            name: file.name,
                            originFileObj: file,
                            size: file.size,
                            type: file.type,
                          },
                        ])
                        return false
                      }}
                      onRemove={(file) => {
                        setHighlightBatchFiles((prev) => prev.filter((item) => item.uid !== file.uid))
                      }}
                      fileList={highlightBatchFiles}
                      multiple
                      accept="video/*"
                    >
                      <Button>选择多个视频</Button>
                    </Upload>
                  </Form.Item>
                </>
              )}

              {highlightEntryMode === 'library_batch' && (
                <Form.Item label="选择原片库剧集">
                  <Select
                    value={libraryDramaName}
                    onChange={setLibraryDramaName}
                    options={dramaOptions}
                    placeholder="选择 AI 漫剧名称"
                    showSearch
                    optionFilterProp="label"
                  />
                </Form.Item>
              )}

              <Form.Item label="衔接方式">
                <Radio.Group
                  value={highlightContinuation}
                  onChange={(e) => setHighlightContinuation(e.target.value)}
                >
                  <Radio.Button value="original">接回原片开头</Radio.Button>
                  <Radio.Button value="digital_human">接数字人视频</Radio.Button>
                </Radio.Group>
              </Form.Item>
              {highlightEntryMode === 'single' && highlightContinuation === 'digital_human' && (
                <Form.Item label="数字人衔接视频">
                  <Upload
                    beforeUpload={(file: RcFile) => {
                      setConnectorFile({
                        uid: file.uid,
                        name: file.name,
                        originFileObj: file,
                        size: file.size,
                        type: file.type,
                      })
                      return false
                    }}
                    onRemove={() => setConnectorFile(null)}
                    fileList={connectorFile ? [connectorFile] : []}
                    maxCount={1}
                    accept="video/*"
                  >
                    <Button>选择数字人视频</Button>
                  </Upload>
                </Form.Item>
              )}
              {highlightEntryMode !== 'single' && highlightContinuation === 'digital_human' && (
                <Form.Item label="选择数字人库视频">
                  <Select
                    value={batchConnectorAssetId}
                    onChange={setBatchConnectorAssetId}
                    options={connectorOptions}
                    placeholder="选择一个数字人视频"
                    showSearch
                    optionFilterProp="label"
                  />
                </Form.Item>
              )}
              <Button
                type="primary"
                block
                onClick={() => {
                  if (highlightEntryMode === 'single') {
                    handleVideoUpload('highlight_extract')
                  } else if (highlightEntryMode === 'local_batch') {
                    handleLocalBatchHighlight()
                  } else {
                    handleLibraryBatchHighlight()
                  }
                }}
                disabled={
                  highlightEntryMode === 'single'
                    ? !highlightFile || (highlightContinuation === 'digital_human' && !connectorFile)
                    : highlightEntryMode === 'local_batch'
                      ? highlightBatchFiles.length === 0 ||
                        !batchDramaName.trim() ||
                        (highlightContinuation === 'digital_human' && !batchConnectorAssetId)
                      : !libraryDramaName ||
                        (highlightContinuation === 'digital_human' && !batchConnectorAssetId)
                }
                loading={highlightUploading}
              >
                {highlightEntryMode === 'single'
                  ? '上传并提取高光'
                  : highlightEntryMode === 'local_batch'
                    ? '上传入库并批量提取'
                    : '从素材库批量提取'}
              </Button>
            </Form>
          </Spin>
        </Card>

        <Card title="上传脚本" hoverable>
          <p style={{ color: '#64748b', fontSize: 13, marginBottom: 12 }}>
            手工撰写或粘贴文案，自己定义每段视觉与文案。
          </p>
          <Button type="primary" block onClick={() => setScriptModalOpen(true)}>
            撰写脚本
          </Button>
        </Card>

        <Card title="打开已有脚本" hoverable>
          <p style={{ color: '#64748b', fontSize: 13, marginBottom: 12 }}>
            查看已创建的草稿、已确认或处理中的脚本。
          </p>
          <Button block onClick={() => setExistingOpen(true)}>
            浏览列表
          </Button>
        </Card>
      </div>

      <Modal
        open={scriptModalOpen}
        title="撰写脚本"
        onCancel={() => setScriptModalOpen(false)}
        onOk={handleScriptSubmit}
        confirmLoading={scriptCreating}
        okText="创建并进入工作区"
        width={640}
        destroyOnHidden
      >
        <p style={{ color: '#64748b', fontSize: 13 }}>
          每行一段，格式：<code>visual | copy</code>
        </p>
        <Input.TextArea
          value={segmentsText}
          onChange={(e) => setSegmentsText(e.target.value)}
          rows={10}
          placeholder={
            '产品近景特写 | 你还在为油皮烦恼吗？\n手部使用演示 | 一抹就能控油 8 小时'
          }
        />
      </Modal>

      <ExistingScriptsModal
        open={existingOpen}
        tenantKey={TENANT_KEY}
        onClose={() => setExistingOpen(false)}
      />
    </div>
  )
}
