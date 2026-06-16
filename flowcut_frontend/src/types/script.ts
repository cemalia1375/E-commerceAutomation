export type ScriptSource = 'decomposed' | 'uploaded'
export type ScriptStatus = 'DRAFT' | 'CONFIRMED' | 'PROCESSING' | 'FAILED'

export interface ScriptSegment {
  idx: number
  start_time: number
  end_time: number
  visual: string
  copy: string
  category?: string
  narrative_role?: string
  hook_strength?: number
  context_dependency?: number
  ending_connectability?: number
  continuity_risk?: number
  ending_state?: string
  open_question?: string
  bridge_text?: string
  candidate_use?: string
  reason?: string
  followup_fit?: {
    original_video?: number
    digital_human?: number
    ad?: number
    reason?: string
  }
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
  details?: Record<string, unknown>
  last_error: string | null
}
