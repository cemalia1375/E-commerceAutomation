import { create } from 'zustand'
import type { ProductNode } from '../types'
import { getProductTree } from '../api/products'

interface ProductTreeState {
  treeNodes: ProductNode[]
  activeProduct: string | null
  activeSceneRole: string | null
  isLoading: boolean
  error: string | null

  fetchTree: (tenantKey: string) => Promise<void>
  selectNode: (product: string | null, sceneRole: string | null) => void
  refreshTree: (tenantKey: string) => Promise<void>
}

export const useProductTreeStore = create<ProductTreeState>((set) => ({
  treeNodes: [],
  activeProduct: null,
  activeSceneRole: null,
  isLoading: false,
  error: null,

  fetchTree: async (tenantKey) => {
    set({ isLoading: true, error: null })
    try {
      const treeNodes = await getProductTree(tenantKey)
      set({ treeNodes, isLoading: false })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载产品树失败'
      set({ error: msg, isLoading: false })
    }
  },

  selectNode: (product, sceneRole) => {
    set({ activeProduct: product, activeSceneRole: sceneRole })
  },

  refreshTree: async (tenantKey) => {
    try {
      const treeNodes = await getProductTree(tenantKey)
      set({ treeNodes })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '刷新产品树失败'
      set({ error: msg })
    }
  },
}))
