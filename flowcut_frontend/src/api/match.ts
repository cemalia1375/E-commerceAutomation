import { apiClient } from './client'
import type { MatchedSegment, MatchCandidate } from '../types'

export interface MatchRequest {
  tenantKey: string
  product: string
  segments: { index: number; description: string; duration: number }[]
}

interface RawCandidate {
  id: number
  name: string
  duration: number
  product: string | null
  scene_role: string | null
  category: string
  score: number
  preview_url: string | null
}

interface RawSegment {
  index: number
  description: string
  duration: number
  phase1: RawCandidate[]
  phase2: RawCandidate[]
  error: string | null
}

function toCandidate(c: RawCandidate): MatchCandidate {
  return {
    id: c.id,
    name: c.name,
    duration: c.duration,
    product: c.product,
    sceneRole: c.scene_role,
    category: c.category,
    score: c.score,
    previewUrl: c.preview_url,
  }
}

export async function matchMaterials(req: MatchRequest): Promise<MatchedSegment[]> {
  const res = await apiClient.post<{ segments: RawSegment[] }>('/materials/match', {
    tenant_key: req.tenantKey,
    product: req.product,
    segments: req.segments,
  })
  return res.data.segments.map((s) => ({
    index: s.index,
    description: s.description,
    duration: s.duration,
    phase1: (s.phase1 ?? []).map(toCandidate),
    phase2: (s.phase2 ?? []).map(toCandidate),
    error: s.error,
  }))
}
