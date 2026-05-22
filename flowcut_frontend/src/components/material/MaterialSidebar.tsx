import { useMemo } from 'react'
import { Tree } from 'antd'
import type { TreeDataNode } from 'antd'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useMaterialStore } from '../../stores/materialStore'
import type { ProductNode } from '../../types'
import styles from './MaterialSidebar.module.css'

const TENANT_KEY = 'flowcut'

function buildAntdTree(nodes: ProductNode[]): TreeDataNode[] {
  return nodes.map((n) => ({
    key: n.product,
    title: `${n.product} (${n.totalCount})`,
    children: n.children.map((c) => ({
      // Use Unit Separator (\x1f) as delimiter — never appears in product/sceneRole names
      key: `${n.product}\x1f${c.sceneRole}`,
      title: `${c.sceneRole} (${c.count})`,
      isLeaf: true,
    })),
  }))
}

export default function MaterialSidebar() {
  const { treeNodes, activeProduct, activeSceneRole, selectNode } =
    useProductTreeStore()
  const fetchMaterials = useMaterialStore((s) => s.fetchMaterials)

  const treeData = useMemo(() => buildAntdTree(treeNodes), [treeNodes])

  const selectedKeys = useMemo(() => {
    if (activeProduct && activeSceneRole) {
      return [`${activeProduct}\x1f${activeSceneRole}`]
    }
    if (activeProduct) return [activeProduct]
    return []
  }, [activeProduct, activeSceneRole])

  const handleSelect = (keys: React.Key[]) => {
    if (keys.length === 0) {
      selectNode(null, null)
      fetchMaterials(TENANT_KEY)
      return
    }
    const key = String(keys[0])
    if (key.includes('\x1f')) {
      const [product, sceneRole] = key.split('\x1f')
      selectNode(product, sceneRole)
      fetchMaterials(TENANT_KEY, { product, sceneRole })
    } else {
      selectNode(key, null)
      fetchMaterials(TENANT_KEY, { product: key })
    }
  }

  return (
    <aside className={styles.sidebar}>
      <div className={styles.header}>产品</div>
      {treeData.length === 0 ? (
        <div className={styles.empty}>暂无产品，上传素材后自动出现</div>
      ) : (
        <Tree
          treeData={treeData}
          selectedKeys={selectedKeys}
          onSelect={handleSelect}
          defaultExpandAll
          blockNode
        />
      )}
    </aside>
  )
}
