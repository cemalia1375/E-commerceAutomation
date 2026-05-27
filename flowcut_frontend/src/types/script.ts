export type ScriptSource = 'decomposed' | 'uploaded'
export type ScriptStatus = 'DRAFT' | 'CONFIRMED' | 'PROCESSING' | 'FAILED'

export interface ScriptSegment {
  idx: number
  start_time: number
  end_time: number
  visual: string
  copy: string
  category?: string
}

export interface Script {
  id: number
  tenant_key: string
  source: ScriptSource
  reference_video_id: number | null
  product: string | null
  segments: ScriptSegment[]
  status: ScriptStatus
  created_at: string
  updated_at: string
}

export interface MatchedMaterial {
  material_id: number
  name: string
  score: number
  preview_url: string | null
  duration: number
  scene_role: string | null
}

export interface SegmentMatchResult {
  seg_idx: number
  visual: string
  copy: string
  phase1: MatchedMaterial[]
  phase2: MatchedMaterial[]
}

export interface TaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | string
  result_url: string | null
  last_error: string | null
}
