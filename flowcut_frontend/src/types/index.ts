// Shape returned by Gemini scene decompose worker
export interface VideoScene {
  startTime: number   // seconds
  endTime: number     // seconds
  content: string     // semantic description of the segment
  category: '真人口播' | '产品展示'
}

export type ReferenceVideoStatus =
  | 'PROCESSING'
  | 'AWAITING_CLASSIFICATION'
  | 'DECOMPOSED'
  | 'FAILED'

export interface VideoSegment {
  startTime: number       // seconds
  endTime: number
  content: string
  category: '真人口播' | '产品展示'
  sceneRole?: string      // user-assigned, missing until classify
}

export interface ReferenceVideo {
  id: number
  tenantKey: string
  name: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  product?: string
  duration: number
  fileSize: number
  status: ReferenceVideoStatus
  sceneData?: VideoSegment[]
  createdAt: string
}

export type MaterialCategory = '人物' | '产品' | '场景' | '氛围' | '字幕板' | '真人口播' | '产品展示'
export type MaterialStatus = 'PROCESSING' | 'READY' | 'FAILED'
export type MaterialType = 'video' | 'image' | 'audio'

export interface Material {
  id: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  previewUrl?: string
  name: string
  transcript?: string
  sceneData?: VideoScene[]
  category: MaterialCategory
  product?: string
  sceneRole?: string
  duration: number
  fileSize: number
  status: MaterialStatus
  usageCount: number
  createdAt: string
  type: MaterialType
}

export interface AudioAsset {
  id: string
  name: string
  category: 'BGM' | '音效'
  audioDuration: string
  fileSize: number
  status: MaterialStatus
  createdAt: string
}

export type CreativeStatus = 'DRAFT' | 'PENDING' | 'ACTIVE'
export type CreativeStatusLabel = '投放中' | '待上架' | '草稿' | '全部'

export interface Creative {
  id: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  name: string
  duration: number
  status: CreativeStatus
  srtUrl?: string
  srtLineCount?: number
  createdAt: string
}

/**
 * 素材↔成片关联（素材在一条成片中的用量和投放数据）
 */
export interface MaterialUsage {
  materialId: string
  creativeId: string
  cost: number        // 消耗（元）
  impressions: number // 展现量
  clicks: number      // 点击量
  conversions: number // 转化数
  roi: number         // ROI（如 1.8）
}

/**
 * 素材详情：包含关联成片列表
 */
export interface MaterialDetail extends Material {
  relatedCreatives: (Creative & { usage: MaterialUsage })[]
}

/**
 * 成片详情：包含使用素材列表
 */
export interface CreativeDetail extends Creative {
  materials: (Material & { usage: MaterialUsage })[]
  totalCost: number
  totalImpressions: number
  totalClicks: number
  totalConversions: number
  overallRoi: number
}

/**
 * 看板每日指标
 */
export interface DailyMetrics {
  date: string
  cost: number
  impressions: number
  clicks: number
  conversions: number
  roi: number
  creativeOutput: number
}

export interface SceneSegment {
  startSec: number   // seconds, sub-second precision (e.g. 3.96)
  endSec: number
  label: string
  description: string
  category: '真人口播' | '产品展示'
}

export interface Script {
  id: string
  name: string
  hook: string
  durationSec: number
  scenes: SceneSegment[]
}

export type GenerateStep = 1 | 2 | 3 | 4 | 5

export interface MatchCandidate {
  id: number
  name: string
  duration: number
  product: string | null
  sceneRole: string | null
  category: string
  score: number
  previewUrl: string | null
}

export interface MatchedSegment {
  index: number
  description: string
  duration: number
  phase1: MatchCandidate[]
  phase2: MatchCandidate[]
  error: string | null
}

export type MessageRole = 'agent' | 'user'
export type MessageType = 'text' | 'progress'

export interface ChatMessage {
  id: string
  role: MessageRole
  type: MessageType
  content: string
  label?: string
  subLabel?: string
  done?: boolean
}

// 素材库产品分层树（与后端 GET /materials/tree 一致）
export interface SceneRoleNode {
  sceneRole: string
  count: number
}

export interface ProductNode {
  product: string
  totalCount: number
  children: SceneRoleNode[]
}

// zip 上传预览项（与后端 POST /materials/upload-zip 响应一致）
export type ZipPreviewStatus = 'existing' | 'new' | 'ignored'

export interface ZipPreviewItem {
  product: string | null
  sceneRole: string | null
  files: string[]
  status: ZipPreviewStatus
}

export interface ZipUploadResponse {
  uploadId: string
  preview: ZipPreviewItem[]
}

export interface ZipOverride {
  index: number
  product: string
  sceneRole: string | null
}
