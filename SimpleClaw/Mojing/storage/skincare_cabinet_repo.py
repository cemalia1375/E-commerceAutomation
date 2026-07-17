"""SkincareCabinetRepository — 护肤柜产品资产表封装。"""

from __future__ import annotations

import json
from typing import Any

from Mojing.storage.database import Database


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return None


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


class SkincareCabinetRepository:
    """读写 `nb_skincare_cabinet_product`。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def find_pending_by_name(
        self,
        *,
        user_id: str,
        brand: str,
        product_name: str,
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, brand, product_name, category, core_efficacy,
                           core_ingredients, risk_ingredients, commercial_image,
                           expiration_date, storage_conditions, specifications,
                           user_photo, in_cabinet, usage_status, opened_date,
                           opened_expiry, create_time, update_time
                    FROM nb_skincare_cabinet_product
                    WHERE user_id=%s
                      AND brand=%s
                      AND product_name=%s
                      AND in_cabinet=0
                      AND deleted=0
                    ORDER BY update_time DESC, id DESC
                    LIMIT 1
                    """,
                    (user_id, brand, product_name),
                )
                row = await cur.fetchone()
        return _row_to_record(row)

    async def find_latest_by_name(
        self,
        *,
        user_id: str,
        brand: str,
        product_name: str,
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, brand, product_name, category, core_efficacy,
                           core_ingredients, risk_ingredients, commercial_image,
                           expiration_date, storage_conditions, specifications,
                           user_photo, in_cabinet, usage_status, opened_date,
                           opened_expiry, create_time, update_time
                    FROM nb_skincare_cabinet_product
                    WHERE user_id=%s
                      AND brand=%s
                      AND product_name=%s
                      AND deleted=0
                    ORDER BY update_time DESC, id DESC
                    LIMIT 1
                    """,
                    (user_id, brand, product_name),
                )
                row = await cur.fetchone()
        return _row_to_record(row)

    async def save_researched_product(
        self,
        *,
        user_id: str,
        brand: str,
        product_name: str,
        usage_status: str | None = None,
        image_url: str = "",
        category: str = "",
        core_efficacy: Any = None,
        core_ingredients: Any = None,
        risk_ingredients: Any = None,
        commercial_image: str = "",
        expiration_date: str | None = None,
        storage_conditions: str = "",
        specifications: str = "",
        creator: str = "research_skincare_product",
    ) -> int:
        existing = await self.find_pending_by_name(
            user_id=user_id,
            brand=brand,
            product_name=product_name,
        )
        efficacy_json = _json_text(core_efficacy)
        ingredients_json = _json_text(core_ingredients)
        risk_json = _json_text(risk_ingredients)
        usage = str(usage_status or "").strip() or "using"

        if existing is not None:
            product_id = int(existing["id"])
            async with self._db.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE nb_skincare_cabinet_product
                        SET category=%s,
                            core_efficacy=%s,
                            core_ingredients=%s,
                            risk_ingredients=%s,
                            commercial_image=%s,
                            expiration_date=%s,
                            storage_conditions=%s,
                            specifications=%s,
                            user_photo=%s,
                            usage_status=%s,
                            updater=%s
                        WHERE id=%s AND user_id=%s
                        """,
                        (
                            category,
                            efficacy_json,
                            ingredients_json,
                            risk_json,
                            commercial_image,
                            expiration_date,
                            storage_conditions,
                            specifications,
                            image_url,
                            usage,
                            creator,
                            product_id,
                            user_id,
                        ),
                    )
            return product_id

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_skincare_cabinet_product
                        (user_id, brand, product_name, category, core_efficacy,
                         core_ingredients, risk_ingredients, commercial_image,
                         expiration_date, storage_conditions, specifications,
                         user_photo, in_cabinet, usage_status, creator, updater)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
                    """,
                    (
                        user_id,
                        brand,
                        product_name,
                        category,
                        efficacy_json,
                        ingredients_json,
                        risk_json,
                        commercial_image,
                        expiration_date,
                        storage_conditions,
                        specifications,
                        image_url,
                        usage,
                        creator,
                        creator,
                    ),
                )
                return int(getattr(cur, "lastrowid", 0) or 0)

    async def mark_in_cabinet(
        self,
        *,
        product_id: int,
        user_id: str,
        usage_status: str | None = None,
        updater: str = "confirm_skincare_cabinet_record",
    ) -> dict[str, Any] | None:
        usage = str(usage_status or "").strip()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                if usage:
                    await cur.execute(
                        """
                        UPDATE nb_skincare_cabinet_product
                        SET in_cabinet=1,
                            usage_status=%s,
                            updater=%s
                        WHERE id=%s AND user_id=%s AND deleted=0
                        """,
                        (usage, updater, product_id, user_id),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE nb_skincare_cabinet_product
                        SET in_cabinet=1,
                            updater=%s
                        WHERE id=%s AND user_id=%s AND deleted=0
                        """,
                        (updater, product_id, user_id),
                    )
        return await self.get(product_id=product_id, user_id=user_id)

    async def get(self, *, product_id: int, user_id: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, brand, product_name, category, core_efficacy,
                           core_ingredients, risk_ingredients, commercial_image,
                           expiration_date, storage_conditions, specifications,
                           user_photo, in_cabinet, usage_status, opened_date,
                           opened_expiry, create_time, update_time
                    FROM nb_skincare_cabinet_product
                    WHERE id=%s AND user_id=%s AND deleted=0
                    LIMIT 1
                    """,
                    (product_id, user_id),
                )
                row = await cur.fetchone()
        return _row_to_record(row)

    async def list_in_cabinet(
        self,
        *,
        user_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return await self.list_by_cabinet_scope(
            user_id=user_id,
            scope="in_cabinet",
            limit=limit,
        )

    async def list_by_cabinet_scope(
        self,
        *,
        user_id: str,
        scope: str = "in_cabinet",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 5), 20))
        clean_scope = str(scope or "").strip() or "in_cabinet"
        if clean_scope == "in_cabinet":
            cabinet_clause = "AND in_cabinet=1"
        elif clean_scope == "researched_not_recorded":
            cabinet_clause = "AND in_cabinet=0"
        elif clean_scope == "all_researched":
            cabinet_clause = ""
        else:
            raise ValueError(f"unsupported skincare cabinet scope: {clean_scope}")

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT id, user_id, brand, product_name, category, core_efficacy,
                           core_ingredients, risk_ingredients, commercial_image,
                           expiration_date, storage_conditions, specifications,
                           user_photo, in_cabinet, usage_status, opened_date,
                           opened_expiry, create_time, update_time
                    FROM nb_skincare_cabinet_product
                    WHERE user_id=%s
                      {cabinet_clause}
                      AND deleted=0
                    ORDER BY update_time DESC, id DESC
                    LIMIT {limit}
                    """,
                    (user_id,),
                )
                rows = await cur.fetchall()
        records: list[dict[str, Any]] = []
        for row in rows or []:
            record = _row_to_record(row)
            if record is not None:
                records.append(record)
        return records


def _row_to_record(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "user_id": str(row[1]),
        "brand": row[2] or "",
        "product_name": row[3] or "",
        "category": row[4] or "",
        "core_efficacy": _parse_json(row[5]) or [],
        "core_ingredients": _parse_json(row[6]) or [],
        "risk_ingredients": _parse_json(row[7]) or [],
        "commercial_image": row[8] or "",
        "expiration_date": str(row[9]) if row[9] else None,
        "storage_conditions": row[10] or "",
        "specifications": row[11] or "",
        "user_photo": row[12] or "",
        "in_cabinet": int(row[13] or 0),
        "usage_status": row[14] or "using",
        "opened_date": str(row[15]) if row[15] else None,
        "opened_expiry": str(row[16]) if row[16] else None,
        "create_time": str(row[17]) if row[17] else None,
        "update_time": str(row[18]) if row[18] else None,
    }
