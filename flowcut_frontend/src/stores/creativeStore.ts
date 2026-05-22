import { create } from 'zustand'
import type { Creative, CreativeStatus, CreativeStatusLabel } from '../types'
import { mockCreatives } from '../mocks/creatives'

interface CreativeState {
  creatives: Creative[]
  activeSubTab: 'video' | 'srt'
  activeStatus: CreativeStatusLabel

  setSubTab: (tab: 'video' | 'srt') => void
  setStatus: (status: CreativeStatusLabel) => void
  filteredCreatives: () => Creative[]
}

export const useCreativeStore = create<CreativeState>((set, get) => ({
  creatives: mockCreatives,
  activeSubTab: 'video',
  activeStatus: '全部',

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
}))
