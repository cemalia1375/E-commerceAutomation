import { useMemo, useState } from 'react'
import { Tree, Popconfirm, message } from 'antd'
import type { TreeDataNode } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useMaterialStore } from '../../stores/materialStore'
import { deleteMaterialsByProduct } from '../../api/materials'
import type { ProductNode } from '../../types'
import styles from './MaterialSidebar.module.css'

const TENANT_KEY = 'flowcut'

interface ProductTitleProps {
  product: string
  totalCount: number
  isDeleting: boolean
  onConfirmDelete: (product: string) => void
}

function ProductTitle({
  product,
  totalCount,
  isDeleting,
  onConfirmDelete,
}: ProductTitleProps) {
  return (
    <span className={styles.productTitle}>
      <span>{`${product} (${totalCount})`}</span>
      <Popconfirm
        title={`确定要删除产品「${product}」下所有素材吗？`}
        description={`此操作不可撤销（${totalCount} 条）。`}
        okText="删除"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: isDeleting }}
        onConfirm={() => onConfirmDelete(product)}
      >
        <DeleteOutlined
          className={styles.deleteIcon}
          onClick={(e) => e.stopPropagation()}
        />
      </Popconfirm>
    </span>
  )
}

export default function MaterialSidebar() {
  const {
    treeNodes,
    activeProduct,
    activeSceneRole,
    selectNode,
    refreshTree,
  } = useProductTreeStore()
  const fetchMaterials = useMaterialStore((s) => s.fetchMaterials)
  const [deletingProduct, setDeletingProduct] = useState<string | null>(null)

  const handleConfirmDelete = async (product: string) => {
    setDeletingProduct(product)
    try {
      const result = await deleteMaterialsByProduct(TENANT_KEY, product)
      message.success(`已删除 ${result.deleted} 条`)
      if (result.errors.length > 0) {
        message.warning(`${result.errors.length} 条删除失败，请查看后端日志`)
      }
      // 如果当前选中的就是被删的产品，回到全部
      if (activeProduct === product) {
        selectNode(null, null)
        await fetchMaterials(TENANT_KEY)
      } else {
        await fetchMaterials(TENANT_KEY, {
          product: activeProduct ?? undefined,
          sceneRole: activeSceneRole ?? undefined,
        })
      }
      await refreshTree(TENANT_KEY)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '删除失败'
      message.error(msg)
    } finally {
      setDeletingProduct(null)
    }
  }

  const treeData = useMemo<TreeDataNode[]>(
    () =>
      treeNodes.map((n: ProductNode) => ({
        key: n.product,
        title: (
          <ProductTitle
            product={n.product}
            totalCount={n.totalCount}
            isDeleting={deletingProduct === n.product}
            onConfirmDelete={handleConfirmDelete}
          />
        ),
        children: n.children.map((c) => ({
          key: `${n.product}\x1f${c.sceneRole}`,
          title: `${c.sceneRole} (${c.count})`,
          isLeaf: true,
        })),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [treeNodes, deletingProduct],
  )

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
