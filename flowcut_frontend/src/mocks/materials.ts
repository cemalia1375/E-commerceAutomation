import type { Material, AudioAsset } from '../types'

export const mockMaterials: Material[] = [
  { id: 'm1', ossKey: '', ossUrl: '', name: '人物素材-3s-主播开瓶介绍产品',   category: '人物',  duration: 3,  fileSize: 12_000_000, status: 'READY',      usageCount: 12, createdAt: '2026-05-11T00:00:00', type: 'video' },
  { id: 'm2', ossKey: '', ossUrl: '', name: '产品素材-5s-精华瓶旋转特写',     category: '产品',  duration: 5,  fileSize: 28_000_000, status: 'READY',      usageCount: 8,  createdAt: '2026-05-11T00:00:00', type: 'video' },
  { id: 'm3', ossKey: '', ossUrl: '', name: '场景素材-8s-室外自然光氛围',     category: '场景',  duration: 8,  fileSize: 45_000_000, status: 'READY',      usageCount: 3,  createdAt: '2026-05-10T00:00:00', type: 'video' },
  { id: 'm4', ossKey: '', ossUrl: '', name: '人物素材-4s-主播使用涂抹过程',   category: '人物',  duration: 4,  fileSize: 19_000_000, status: 'PROCESSING', usageCount: 0,  createdAt: '2026-05-11T00:00:00', type: 'video' },
  { id: 'm5', ossKey: '', ossUrl: '', name: '氛围素材-2s-白光转场过渡',       category: '氛围',  duration: 2,  fileSize: 8_000_000,  status: 'READY',      usageCount: 21, createdAt: '2026-05-10T00:00:00', type: 'video' },
  { id: 'm6', ossKey: '', ossUrl: '', name: '产品素材-6s-整套护肤品包装展示', category: '产品',  duration: 6,  fileSize: 33_000_000, status: 'READY',      usageCount: 5,  createdAt: '2026-05-10T00:00:00', type: 'video' },
  { id: 'm7', ossKey: '', ossUrl: '', name: '场景素材-3s-睡前护肤桌面特写',   category: '场景',  duration: 3,  fileSize: 16_000_000, status: 'READY',      usageCount: 7,  createdAt: '2026-05-10T00:00:00', type: 'video' },
  { id: 'm8', ossKey: '', ossUrl: '', name: '产品素材-4s-精华液滴落慢动作',   category: '产品',  duration: 4,  fileSize: 22_000_000, status: 'FAILED',     usageCount: 0,  createdAt: '2026-05-10T00:00:00', type: 'video' },
  { id: 'i1', ossKey: '', ossUrl: '', name: '产品主图-精华瓶正面白底',        category: '产品',  duration: 0,  fileSize: 2_100_000,  status: 'READY',      usageCount: 4,  createdAt: '2026-05-11T00:00:00', type: 'image' },
  { id: 'i2', ossKey: '', ossUrl: '', name: '背景图-米白色纹理简约',          category: '场景',  duration: 0,  fileSize: 1_400_000,  status: 'READY',      usageCount: 2,  createdAt: '2026-05-11T00:00:00', type: 'image' },
  { id: 'i3', ossKey: '', ossUrl: '', name: '字幕板-限时折扣贴片',            category: '字幕板', duration: 0, fileSize: 300_000,    status: 'READY',      usageCount: 9,  createdAt: '2026-05-11T00:00:00', type: 'image' },
]

export const mockAudioMaterials: AudioAsset[] = [
  { id: 'a1', name: '轻快活力-电商BGM-01',  category: 'BGM',  audioDuration: '2:34', fileSize: 3_200_000, status: 'READY', createdAt: '2026-05-11T00:00:00' },
  { id: 'a2', name: '温柔治愈-护肤氛围-02', category: 'BGM',  audioDuration: '3:12', fileSize: 4_500_000, status: 'READY', createdAt: '2026-05-11T00:00:00' },
  { id: 'a3', name: '转场音效-闪光-01',     category: '音效', audioDuration: '0:02', fileSize: 100_000,   status: 'READY', createdAt: '2026-05-11T00:00:00' },
]
