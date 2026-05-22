import { Input } from 'antd'
import type { ZipPreviewItem, ProductNode } from '../../types'
import styles from './ZipPreview.module.css'

interface ZipPreviewProps {
  preview: ZipPreviewItem[]
  edits: Record<number, { product: string; sceneRole: string | null }>
  onEdit: (index: number, product: string, sceneRole: string | null) => void
  existingTree: ProductNode[]
}

function computeStatus(
  product: string,
  sceneRole: string | null,
  existingTree: ProductNode[],
): 'existing' | 'new' {
  const productNode = existingTree.find((n) => n.product === product)
  if (!productNode) return 'new'
  if (sceneRole === null) return 'existing'
  const hasRole = productNode.children.some((c) => c.sceneRole === sceneRole)
  return hasRole ? 'existing' : 'new'
}

function statusBadge(status: 'existing' | 'new' | 'ignored'): { label: string; cls: string } {
  if (status === 'existing') return { label: '已有', cls: styles.badgeExisting }
  if (status === 'new') return { label: '新建', cls: styles.badgeNew }
  return { label: '已忽略', cls: styles.badgeIgnored }
}

function groupCls(status: 'existing' | 'new' | 'ignored'): string {
  if (status === 'existing') return styles.statusExisting
  if (status === 'new') return styles.statusNew
  return styles.statusIgnored
}

export default function ZipPreview({ preview, edits, onEdit, existingTree }: ZipPreviewProps) {
  const totalFiles = preview
    .filter((p) => p.status !== 'ignored')
    .reduce((acc, p) => acc + p.files.length, 0)

  let newNodes = 0
  let existingNodes = 0
  preview.forEach((item, idx) => {
    if (item.status === 'ignored') return
    const edit = edits[idx]
    const product = edit?.product ?? item.product ?? ''
    const sceneRole = edit?.sceneRole ?? item.sceneRole ?? null
    const computed = computeStatus(product, sceneRole, existingTree)
    if (computed === 'new') newNodes++
    else existingNodes++
  })

  return (
    <>
      <div className={styles.container}>
        {preview.map((item, idx) => {
          if (item.status === 'ignored') {
            const badge = statusBadge('ignored')
            return (
              <div key={idx} className={styles.group}>
                <div className={`${styles.groupHeader} ${groupCls('ignored')}`}>
                  <span>已忽略文件</span>
                  <span className={`${styles.badge} ${badge.cls}`}>{badge.label}</span>
                </div>
                <div className={styles.files}>{item.files.join(' · ')}</div>
              </div>
            )
          }

          const edit = edits[idx]
          const currentProduct = edit?.product ?? item.product ?? ''
          const currentSceneRole = edit !== undefined ? edit.sceneRole : item.sceneRole
          const computed = computeStatus(currentProduct, currentSceneRole, existingTree)
          const badge = statusBadge(computed)

          return (
            <div key={idx} className={styles.group}>
              <div className={`${styles.groupHeader} ${groupCls(computed)}`}>
                <div style={{ display: 'flex', gap: 6, flex: 1, alignItems: 'center' }}>
                  <Input
                    size="small"
                    value={currentProduct}
                    placeholder="产品名"
                    onChange={(e) => onEdit(idx, e.target.value, currentSceneRole)}
                    style={{ width: 140 }}
                  />
                  <span style={{ color: '#999', fontSize: 12 }}>/</span>
                  <Input
                    size="small"
                    value={currentSceneRole ?? ''}
                    placeholder="场景角色（可选）"
                    onChange={(e) =>
                      onEdit(idx, currentProduct, e.target.value || null)
                    }
                    style={{ width: 160 }}
                    allowClear
                  />
                </div>
                <span className={`${styles.badge} ${badge.cls}`}>{badge.label}</span>
              </div>
              <div className={styles.files}>{item.files.join(' · ')}</div>
            </div>
          )
        })}
      </div>
      <div className={styles.summary}>
        共 {totalFiles} 个文件，{existingNodes} 个已有节点，{newNodes} 个新建节点
      </div>
    </>
  )
}
