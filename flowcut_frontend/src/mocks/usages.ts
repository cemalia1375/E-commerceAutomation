import type { MaterialUsage } from '../types'

/**
 * 素材 ↔ 成片关联 mock 数据
 * materialId → multiple creativeIds with per-creative metrics
 */
export const mockMaterialUsages: MaterialUsage[] = [
  // c1 (护肤品-痛点版-v1) 使用的素材
  { materialId: 'm1', creativeId: 'c1', cost: 320, impressions: 45_000, clicks: 2_100, conversions: 180, roi: 2.8 },
  { materialId: 'm2', creativeId: 'c1', cost: 280, impressions: 38_000, clicks: 1_800, conversions: 150, roi: 2.6 },
  { materialId: 'm5', creativeId: 'c1', cost: 150, impressions: 22_000, clicks: 950,  conversions: 85,  roi: 3.1 },
  { materialId: 'm7', creativeId: 'c1', cost: 190, impressions: 26_000, clicks: 1_200, conversions: 110, roi: 2.9 },

  // c2 (护肤品-场景版-v1) 使用的素材
  { materialId: 'm1', creativeId: 'c2', cost: 180, impressions: 28_000, clicks: 1_300, conversions: 95,  roi: 2.4 },
  { materialId: 'm3', creativeId: 'c2', cost: 120, impressions: 18_000, clicks: 780,  conversions: 60,  roi: 2.2 },
  { materialId: 'm6', creativeId: 'c2', cost: 95,  impressions: 14_000, clicks: 620,  conversions: 48,  roi: 2.5 },

  // c3 (面膜-科普版-v2) 使用的素材
  { materialId: 'm2', creativeId: 'c3', cost: 410, impressions: 62_000, clicks: 3_100, conversions: 290, roi: 3.4 },
  { materialId: 'm5', creativeId: 'c3', cost: 230, impressions: 35_000, clicks: 1_600, conversions: 145, roi: 3.2 },
  { materialId: 'i1', creativeId: 'c3', cost: 80,  impressions: 12_000, clicks: 520,  conversions: 42,  roi: 2.8 },

  // c5 (面霜-对比版-v1) 使用的素材
  { materialId: 'm1', creativeId: 'c5', cost: 260, impressions: 40_000, clicks: 1_900, conversions: 165, roi: 3.0 },
  { materialId: 'm6', creativeId: 'c5', cost: 175, impressions: 25_000, clicks: 1_100, conversions: 88,  roi: 2.7 },
  { materialId: 'i2', creativeId: 'c5', cost: 60,  impressions: 9_000,  clicks: 380,  conversions: 30,  roi: 2.3 },
]
