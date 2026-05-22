import { create } from 'zustand'
import type { Material, MaterialType } from '../types'
import { listMaterials } from '../api/materials'

interface MaterialState {
  materials: Material[]
  isLoading: boolean
  error: string | null
  activeSubTab: MaterialType

  setSubTab: (tab: MaterialType) => void
  filteredMaterials: () => Material[]
  audioMaterials: () => Material[]
  fetchMaterials: (
    tenantKey: string,
    filters?: { product?: string; sceneRole?: string },
  ) => Promise<void>
  addMaterial: (material: Material) => void
  addMaterials: (materials: Material[]) => void
}

export const useMaterialStore = create<MaterialState>((set, get) => ({
  materials: [],
  isLoading: false,
  error: null,
  activeSubTab: 'video',

  setSubTab: (tab) => set({ activeSubTab: tab }),

  filteredMaterials: () => {
    const { materials, activeSubTab } = get()
    return materials.filter((m) => m.type === activeSubTab)
  },

  audioMaterials: () => get().materials.filter((m) => m.type === 'audio'),

  fetchMaterials: async (tenantKey, filters) => {
    set({ isLoading: true, error: null })
    try {
      const materials = await listMaterials(tenantKey, filters)
      set({ materials, isLoading: false })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      set({ error: msg, isLoading: false })
    }
  },

  addMaterial: (material) =>
    set((s) => ({ materials: [material, ...s.materials] })),

  addMaterials: (materials) =>
    set((s) => ({ materials: [...materials, ...s.materials] })),
}))
