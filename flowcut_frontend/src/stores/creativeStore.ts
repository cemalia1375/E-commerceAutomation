import { create } from 'zustand'
import type { Creative, CreativeStatus, CreativeStatusLabel } from '../types'
import { mockCreatives } from '../mocks/creatives'
import { listCreatives } from '../api/qianchuan'

const DEFAULT_TENANT_KEY = import.meta.env.VITE_TENANT_KEY ?? 'flowcut'

interface CreativeState {
  creatives: Creative[]
  activeSubTab: 'video' | 'srt' | 'highlight'
  activeStatus: CreativeStatusLabel
  loading: boolean

  setSubTab: (tab: 'video' | 'srt' | 'highlight') => void
  setStatus: (status: CreativeStatusLabel) => void
  filteredCreatives: () => Creative[]
  refetch: () => Promise<void>
}

export const useCreativeStore = create<CreativeState>((set, get) => ({
  creatives: mockCreatives,
  activeSubTab: 'video',
  activeStatus: '全部',
  loading: false,

  setSubTab: (tab) => set({ activeSubTab: tab }),
  setStatus: (status) => set({ activeStatus: status }),

  filteredCreatives: () => {
    const { creatives, activeStatus } = get()
    if (activeStatus === '全部') return creatives
    const map: Record<Exclude<CreativeStatusLabel, '全部'>, CreativeStatus> = {
      '投放中': 'ACTIVE',
      '待上架': 'PENDING',
      '草稿': 'DRAFT',
    }
    return creatives.filter((c) => c.status === map[activeStatus as Exclude<CreativeStatusLabel, '全部'>])
  },

  refetch: async () => {
    set({ loading: true })
    try {
      const creatives = await listCreatives(DEFAULT_TENANT_KEY)
      set({ creatives })
    } catch {
      // 后端不可用时保持现有列表，不中断 UI
    } finally {
      set({ loading: false })
    }
  },
}))
