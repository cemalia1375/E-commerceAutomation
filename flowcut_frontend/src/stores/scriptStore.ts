import { create } from 'zustand'
import type { Script, ScriptSegment, SegmentMatchResult } from '../types/script'

interface ScriptState {
  currentScript: Script | null
  matchResults: SegmentMatchResult[]
  selectedMaterials: Set<number>
  exportTaskId: string | null

  setScript: (script: Script | null) => void
  updateSegments: (segments: ScriptSegment[]) => void
  setMatchResults: (results: SegmentMatchResult[]) => void
  toggleMaterial: (materialId: number) => void
  setExportTaskId: (taskId: string | null) => void
  reset: () => void
}

export const useScriptStore = create<ScriptState>((set) => ({
  currentScript: null,
  matchResults: [],
  selectedMaterials: new Set<number>(),
  exportTaskId: null,

  setScript: (script) => set({ currentScript: script }),
  updateSegments: (segments) =>
    set((s) =>
      s.currentScript
        ? { currentScript: { ...s.currentScript, segments } }
        : s,
    ),
  setMatchResults: (results) =>
    set(() => {
      const ids = new Set<number>()
      for (const r of results) {
        for (const m of [...r.phase1, ...r.phase2]) ids.add(m.material_id)
      }
      return { matchResults: results, selectedMaterials: ids }
    }),
  toggleMaterial: (id) =>
    set((s) => {
      const next = new Set(s.selectedMaterials)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedMaterials: next }
    }),
  setExportTaskId: (taskId) => set({ exportTaskId: taskId }),
  reset: () =>
    set({
      currentScript: null,
      matchResults: [],
      selectedMaterials: new Set<number>(),
      exportTaskId: null,
    }),
}))
