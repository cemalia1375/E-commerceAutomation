import type { Script } from '../types'

export const mockScripts: Script[] = [
  {
    id: 's1',
    name: '痛点开场版',
    hook: '「你有没有遇过用了很多护肤品，皮肤还是干的问题…」',
    durationSec: 36,
    scenes: [
      { startSec: 0,  endSec: 3,  label: '钩子',    description: '主播正面，口播痛点引入',    category: '真人口播' },
      { startSec: 3,  endSec: 8,  label: '痛点展示', description: '手部特写，演示皮肤干燥状态', category: '产品展示' },
      { startSec: 8,  endSec: 14, label: '产品引入', description: '包装特写 + 口播成分介绍',    category: '产品展示' },
      { startSec: 14, endSec: 22, label: '使用过程', description: '主播涂抹，展示产品质地',    category: '真人口播' },
      { startSec: 22, endSec: 30, label: '效果对比', description: '使用前后皮肤状态特写',      category: '产品展示' },
      { startSec: 30, endSec: 36, label: 'CTA',     description: '口播促单 + 价格展示',        category: '真人口播' },
    ],
  },
  {
    id: 's2',
    name: '场景代入版',
    hook: '「冬天皮肤干到脱皮，这瓶精华让我找回了光泽感…」',
    durationSec: 33,
    scenes: [
      { startSec: 0, endSec: 4, label: '场景', description: '冬日室内，主播坐在窗边，自然光', category: '真人口播' },
    ],
  },
  {
    id: 's3',
    name: '成分科普版',
    hook: '「为什么同样是保湿，玻尿酸和神经酰胺差这么多…」',
    durationSec: 38,
    scenes: [],
  },
  {
    id: 's4',
    name: '产品特写版',
    hook: '「这瓶精华的质地真的绝，滴一滴撑一整个冬天…」',
    durationSec: 31,
    scenes: [],
  },
]
