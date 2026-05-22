import type { DailyMetrics } from '../types'

export const mockDailyMetrics: DailyMetrics[] = [
  { date: '05-07', cost: 1280, impressions: 185_000, clicks: 8_200, conversions: 720, roi: 2.6, creativeOutput: 3 },
  { date: '05-08', cost: 1560, impressions: 210_000, clicks: 9_500, conversions: 810, roi: 2.8, creativeOutput: 5 },
  { date: '05-09', cost: 1420, impressions: 198_000, clicks: 8_800, conversions: 760, roi: 2.7, creativeOutput: 4 },
  { date: '05-10', cost: 1880, impressions: 265_000, clicks: 12_100, conversions: 1050, roi: 3.1, creativeOutput: 6 },
  { date: '05-11', cost: 2100, impressions: 290_000, clicks: 13_500, conversions: 1180, roi: 3.3, creativeOutput: 7 },
  { date: '05-12', cost: 1950, impressions: 272_000, clicks: 12_600, conversions: 1090, roi: 3.0, creativeOutput: 5 },
  { date: '05-13', cost: 2340, impressions: 318_000, clicks: 15_200, conversions: 1320, roi: 3.5, creativeOutput: 8 },
]

/** 物料消耗排行 */
export const mockMaterialRanking = [
  { materialId: 'm1', name: '人物素材-3s-主播开瓶', category: '人物', usageCount: 12, totalCost: 760, avgRoi: 2.73 },
  { materialId: 'm2', name: '产品素材-5s-精华瓶旋转', category: '产品', usageCount: 8,  totalCost: 690, avgRoi: 3.0 },
  { materialId: 'm5', name: '氛围素材-2s-白光转场', category: '氛围', usageCount: 21, totalCost: 380, avgRoi: 3.15 },
  { materialId: 'm6', name: '产品素材-6s-包装展示', category: '产品', usageCount: 5,  totalCost: 270, avgRoi: 2.6 },
  { materialId: 'm7', name: '场景素材-3s-桌面特写', category: '场景', usageCount: 7,  totalCost: 190, avgRoi: 2.9 },
]
