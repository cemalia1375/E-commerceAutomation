import { apiClient } from './client'
import type { ProductNode, SceneRoleNode } from '../types'

function fromBackendSceneRole(raw: Record<string, unknown>): SceneRoleNode {
  return {
    sceneRole: raw.scene_role as string,
    count: raw.count as number,
  }
}

function fromBackendProduct(raw: Record<string, unknown>): ProductNode {
  return {
    product: raw.product as string,
    totalCount: raw.total_count as number,
    children: ((raw.children as Record<string, unknown>[]) ?? []).map(
      fromBackendSceneRole,
    ),
  }
}

export async function getProductTree(tenantKey: string): Promise<ProductNode[]> {
  const { data } = await apiClient.get<Record<string, unknown>[]>(
    '/materials/tree',
    { params: { tenant_key: tenantKey } },
  )
  return data.map(fromBackendProduct)
}

export async function getProducts(tenantKey: string): Promise<string[]> {
  const { data } = await apiClient.get<{ products: string[] }>(
    '/materials/products',
    { params: { tenant_key: tenantKey } },
  )
  return data.products
}
