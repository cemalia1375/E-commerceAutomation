import { create } from 'zustand'
import type { Script, ScriptSegment, SegmentMatchResult } from '../types/script'

export type SelectedMaterials = Record<number, number[]>

interface ScriptState {
  currentScript: Script | null
  matchResults: SegmentMatchResult[]
  selectedMaterials: SelectedMaterials
  exportTaskId: string | null

  setScript: (script: Script | null) => void
  updateSegments: (segments: ScriptSegment[]) => void
  setMatchResults: (results: SegmentMatchResult[]) => void
  toggleMaterial: (segIdx: number, materialId: number) => void
  setExportTaskId: (taskId: string | null) => void
  reset: () => void
}

function pickDefaults(results: SegmentMatchResult[]): SelectedMaterials {
  const next: SelectedMaterials = {}
  for (const r of results) {
    if (r.phase1.length > 0) {
      next[r.seg_idx] = [r.phase1[0].material_id]
    } else if (r.phase2.length > 0) {
      next[r.seg_idx] = [r.phase2[0].material_id]
    } else {
      next[r.seg_idx] = []
    }
  }
  return next
}

export const useScriptStore = create<ScriptState>((set) => ({
  currentScript: null,
  matchResults: [],
  selectedMaterials: {},
  exportTaskId: null,

  setScript: (script) => set({ currentScript: script }),
  updateSegments: (segments) =>
    set((s) =>
      s.currentScript
        ? { currentScript: { ...s.currentScript, segments } }
        : s,
    ),
  setMatchResults: (results) =>
    set({ matchResults: results, selectedMaterials: pickDefaults(results) }),
  toggleMaterial: (segIdx, materialId) =>
    set((s) => {
      const current = s.selectedMaterials[segIdx] ?? []
      const exists = current.includes(materialId)
      const nextSeg = exists
        ? current.filter((id) => id !== materialId)
        : [...current, materialId]
      return {
        selectedMaterials: { ...s.selectedMaterials, [segIdx]: nextSeg },
      }
    }),
  setExportTaskId: (taskId) => set({ exportTaskId: taskId }),
  reset: () =>
    set({
      currentScript: null,
      matchResults: [],
      selectedMaterials: {},
      exportTaskId: null,
    }),
}))
