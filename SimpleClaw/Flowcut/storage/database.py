"""FlowCut 的异步 MySQL 连接池。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiomysql


async def ensure_schema(db: "Database") -> None:
    """建表（IF NOT EXISTS），Mojing nb_* 表 + FlowCut fc_* 表，安全幂等。"""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS nb_tenants (
            tenant_id  BIGINT NOT NULL AUTO_INCREMENT,
            tenant_key VARCHAR(255) NOT NULL,
            status     VARCHAR(32)  NOT NULL DEFAULT 'active',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (tenant_id),
            UNIQUE KEY uq_tenant_key (tenant_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_state (
            tenant_key                  VARCHAR(255) PRIMARY KEY,
            primary_session_key         VARCHAR(255) NULL,
            primary_channel             VARCHAR(255) NULL,
            primary_chat_id             VARCHAR(255) NULL,
            last_user_activity_at       DATETIME NULL,
            updated_at                  DATETIME NOT NULL,
            heartbeat_enabled           TINYINT(1) NOT NULL DEFAULT 0,
            heartbeat_interval_s        INT NOT NULL DEFAULT 0,
            heartbeat_next_run_at       DATETIME NULL,
            heartbeat_last_run_at       DATETIME NULL,
            heartbeat_last_status       VARCHAR(32) NULL,
            heartbeat_last_error        TEXT NULL,
            journey_json                JSON NULL,
            last_cron_session_key       VARCHAR(255) NULL,
            last_heartbeat_session_key  VARCHAR(255) NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_cron_jobs (
            id          VARCHAR(64) PRIMARY KEY,
            tenant_key  VARCHAR(255) NULL,
            session_key VARCHAR(255) NOT NULL,
            cron_type   VARCHAR(32)  NOT NULL,
            cron_expr   VARCHAR(128) NULL,
            interval_s  INT NULL,
            run_at      DATETIME NOT NULL,
            task        TEXT NOT NULL,
            status      VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at  DATETIME NOT NULL,
            updated_at  DATETIME NOT NULL,
            last_run_at DATETIME NULL,
            KEY idx_cron_due (status, run_at),
            KEY idx_cron_tenant (tenant_key, status)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_sessions (
            tenant_key          VARCHAR(255) NOT NULL,
            session_key         VARCHAR(255) NOT NULL,
            session_type        VARCHAR(32)  NOT NULL DEFAULT 'main',
            origin_session_key  VARCHAR(255) NULL,
            channel             VARCHAR(64)  NULL,
            chat_id             VARCHAR(255) NULL,
            title               VARCHAR(255) NULL,
            is_primary          TINYINT(1)   NOT NULL DEFAULT 0,
            last_consolidated   INT          NOT NULL DEFAULT 0,
            metadata_json       JSON NULL,
            created_at          DATETIME NOT NULL,
            updated_at          DATETIME NOT NULL,
            PRIMARY KEY (tenant_key, session_key),
            KEY idx_sessions_tenant_type (tenant_key, session_type),
            KEY idx_sessions_origin      (tenant_key, origin_session_key),
            KEY idx_sessions_updated     (tenant_key, updated_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_session_messages (
            tenant_key     VARCHAR(255) NOT NULL,
            session_key    VARCHAR(255) NOT NULL,
            seq            INT NOT NULL,
            content_json   JSON NULL,
            message_json   JSON NULL,
            role           VARCHAR(32)  NULL,
            tool_name      VARCHAR(128) NULL,
            tool_call_id   VARCHAR(128) NULL,
            tokens_estimate INT NULL,
            created_at     DATETIME NOT NULL,
            PRIMARY KEY (tenant_key, session_key, seq),
            KEY idx_messages_role (tenant_key, session_key, role),
            KEY idx_messages_time (tenant_key, session_key, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_documents (
            doc_id       BIGINT NOT NULL AUTO_INCREMENT,
            tenant_key   VARCHAR(255) NOT NULL,
            doc_type     VARCHAR(64)  NOT NULL,
            doc_name     VARCHAR(255) NOT NULL,
            content      LONGTEXT NOT NULL,
            content_hash VARCHAR(64)  NOT NULL,
            format       VARCHAR(16)  NOT NULL DEFAULT 'markdown',
            version_no   INT NOT NULL DEFAULT 1,
            is_active    TINYINT(1)   NOT NULL DEFAULT 1,
            created_by   VARCHAR(64)  NULL,
            updated_by   VARCHAR(64)  NULL,
            created_at   DATETIME NOT NULL,
            updated_at   DATETIME NOT NULL,
            PRIMARY KEY (doc_id),
            UNIQUE KEY uq_tenant_doc_type_name (tenant_key, doc_type, doc_name),
            KEY idx_docs_tenant_type (tenant_key, doc_type),
            KEY idx_docs_active      (tenant_key, is_active)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_document_versions (
            version_id     BIGINT NOT NULL AUTO_INCREMENT,
            doc_id         BIGINT NOT NULL,
            tenant_key     VARCHAR(255) NOT NULL,
            doc_type       VARCHAR(64)  NOT NULL,
            doc_name       VARCHAR(255) NOT NULL,
            version_no     INT NOT NULL,
            content        LONGTEXT NOT NULL,
            content_hash   VARCHAR(64)  NOT NULL,
            change_summary VARCHAR(512) NULL,
            change_source  VARCHAR(64)  NULL,
            operator_id    VARCHAR(255) NULL,
            created_at     DATETIME NOT NULL,
            PRIMARY KEY (version_id),
            KEY idx_doc_versions        (doc_id, version_no),
            KEY idx_doc_versions_tenant (tenant_key, doc_type, doc_name, version_no)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_image_analysis_jobs (
            job_id              VARCHAR(64)  NOT NULL,
            tenant_key          VARCHAR(255) NOT NULL,
            session_key         VARCHAR(255) NOT NULL,
            message_id          VARCHAR(255) NULL,
            image_id            VARCHAR(64)  NOT NULL,
            image_ref           TEXT NOT NULL,
            focus               VARCHAR(128) NOT NULL,
            status              VARCHAR(32)  NOT NULL,
            request_payload_json JSON NULL,
            result_json         JSON NULL,
            summary_text        LONGTEXT NULL,
            external_job_id     VARCHAR(255) NULL,
            last_error          TEXT NULL,
            created_at          DATETIME NOT NULL,
            updated_at          DATETIME NOT NULL,
            started_at          DATETIME NULL,
            completed_at        DATETIME NULL,
            PRIMARY KEY (job_id),
            KEY idx_image_jobs_lookup (tenant_key, session_key, image_id, focus, created_at),
            KEY idx_image_jobs_status (tenant_key, session_key, status, updated_at),
            KEY idx_image_jobs_latest (tenant_key, session_key, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_skin_diary_results (
            id            BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
            tenant_key    VARCHAR(128) NOT NULL,
            analyzed_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            state         VARCHAR(32) NOT NULL,
            summary       TEXT NOT NULL,
            chips         JSON DEFAULT NULL,
            morning_steps JSON DEFAULT NULL,
            evening_steps JSON DEFAULT NULL,
            raw_output    JSON DEFAULT NULL,
            creator       VARCHAR(64) DEFAULT '',
            create_time   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updater       VARCHAR(64) DEFAULT '',
            update_time   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            deleted       BIT(1) DEFAULT b'0',
            tenant_id     BIGINT DEFAULT 0,
            PRIMARY KEY (id),
            KEY idx_tenant_date (tenant_key, analyzed_at DESC)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_skin_diary_sessions (
            id                 VARCHAR(64)  NOT NULL,
            tenant_key         VARCHAR(128) NOT NULL,
            session_key        VARCHAR(256) NOT NULL,
            parent_session_key VARCHAR(256) NOT NULL,
            display_name       VARCHAR(128) NOT NULL DEFAULT '肌肤日记助手',
            status             VARCHAR(32)  NOT NULL DEFAULT 'idle',
            last_active_at     DATETIME NULL,
            created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uk_tenant    (tenant_key),
            KEY idx_session_key     (session_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_slow_model_reports (
            id              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '编号',
            report_id       VARCHAR(64)  NOT NULL COMMENT '报告唯一标识',
            user_id         VARCHAR(255) NOT NULL COMMENT '用户ID',
            session_id      VARCHAR(128) NULL COMMENT '关联会话ID',
            status          VARCHAR(32)  NOT NULL DEFAULT 'pending' COMMENT '状态: pending/done/error',
            model_name      VARCHAR(128) NULL COMMENT '慢模型名称',
            model_version   VARCHAR(64)  NULL COMMENT '慢模型版本',
            trace_id        VARCHAR(128) NULL COMMENT '链路追踪ID',
            overview_json   MEDIUMTEXT   NULL COMMENT 'Tab1骨架(radarDimensions/signal/skinAttribute/stage)',
            decode_json     MEDIUMTEXT   NULL COMMENT 'Tab2骨架(signals[]含name/tags/images)',
            secret_json     MEDIUMTEXT   NULL COMMENT 'Tab3骨架(focusTags[])',
            track_json      MEDIUMTEXT   NULL COMMENT 'Tab4骨架(signalItems[])',
            raw_input_json  MEDIUMTEXT   NULL COMMENT '慢模型原始输入快照',
            summary         TEXT         NULL COMMENT '报告摘要(列表展示)',
            read_status     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '0-未读 1-已读',
            notified        TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '0-未推送 1-已推送',
            creator         VARCHAR(64)  DEFAULT '' COMMENT '创建者',
            create_time     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updater         VARCHAR(64)  DEFAULT '' COMMENT '更新者',
            update_time     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            deleted         TINYINT      NOT NULL DEFAULT 0 COMMENT '是否删除',
            tenant_id       BIGINT       NOT NULL DEFAULT 0 COMMENT '租户编号',
            PRIMARY KEY (id, user_id) USING BTREE,
            UNIQUE KEY uk_report_id (report_id),
            KEY idx_user_id (user_id),
            KEY idx_user_status_time (user_id, status, create_time),
            KEY idx_session_id (session_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='慢模型报告(锚点表)'
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_deep_analysis_reports (
            id               BIGINT       NOT NULL AUTO_INCREMENT COMMENT '编号',
            report_id        VARCHAR(64)  NOT NULL COMMENT '报告唯一标识',
            user_id          VARCHAR(255) NOT NULL COMMENT '用户ID',
            session_id       VARCHAR(64)  NULL COMMENT '关联会话ID',
            status           VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT '状态: pending/done/error',
            strategy_version VARCHAR(32)  NULL COMMENT '深度分析策略版本',
            trace_id         VARCHAR(64)  NULL COMMENT '链路追踪ID',
            overview_json    MEDIUMTEXT   NULL COMMENT 'Tab1数据',
            decode_json      MEDIUMTEXT   NULL COMMENT 'Tab2数据',
            secret_json      MEDIUMTEXT   NULL COMMENT 'Tab3数据',
            track_json       MEDIUMTEXT   NULL COMMENT 'Tab4数据',
            creator          VARCHAR(64)  DEFAULT '' COMMENT '创建者',
            create_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updater          VARCHAR(64)  DEFAULT '' COMMENT '更新者',
            update_time      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            deleted          TINYINT      NOT NULL DEFAULT 0 COMMENT '是否删除',
            tenant_id        BIGINT       NOT NULL DEFAULT 0 COMMENT '租户编号',
            PRIMARY KEY (id, user_id) USING BTREE,
            UNIQUE KEY uk_report_id (report_id),
            KEY idx_user_id (user_id),
            KEY idx_user_status_time (user_id, status, create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='深度分析报告（核心解读 + 护肤步骤）'
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_agent_field_reports (
            id              BIGINT       NOT NULL AUTO_INCREMENT COMMENT '编号',
            report_id       VARCHAR(64)  NOT NULL COMMENT '报告唯一标识',
            user_id         VARCHAR(255) NOT NULL COMMENT '用户ID',
            session_id      VARCHAR(64)  NULL COMMENT '关联会话ID',
            status          VARCHAR(20)  NOT NULL DEFAULT 'pending' COMMENT '状态: pending/done/error',
            trace_id        VARCHAR(64)  NULL COMMENT '链路追踪ID',
            overview_json   MEDIUMTEXT   NULL COMMENT 'Tab1数据',
            decode_json     MEDIUMTEXT   NULL COMMENT 'Tab2数据',
            secret_json     MEDIUMTEXT   NULL COMMENT 'Tab3数据',
            track_json      MEDIUMTEXT   NULL COMMENT 'Tab4数据',
            creator         VARCHAR(64)  DEFAULT '' COMMENT '创建者',
            create_time     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updater         VARCHAR(64)  DEFAULT '' COMMENT '更新者',
            update_time     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            deleted         TINYINT      NOT NULL DEFAULT 0 COMMENT '是否删除',
            tenant_id       BIGINT       NOT NULL DEFAULT 0 COMMENT '租户编号',
            PRIMARY KEY (id, user_id) USING BTREE,
            UNIQUE KEY uk_report_id (report_id),
            KEY idx_user_id (user_id),
            KEY idx_user_status_time (user_id, status, create_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 业务字段报告（UI 展示字段）'
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_topic_tracking (
            tenant_key               VARCHAR(128) PRIMARY KEY,
            topics                   JSON NOT NULL,
            mood                     JSON NULL,
            total_turns              INT NOT NULL DEFAULT 0,
            last_reminder_turn       INT NOT NULL DEFAULT 0,
            last_memory_extract_turn INT NOT NULL DEFAULT 0,
            updated_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_memory_entries (
            id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
            tenant_key         VARCHAR(128) NOT NULL,
            source             VARCHAR(64)  NOT NULL DEFAULT 'main',
            topic              VARCHAR(128) NOT NULL,
            description        VARCHAR(256) NOT NULL,
            content            TEXT NOT NULL,
            token_count        INT NOT NULL DEFAULT 0,
            last_referenced_at DATETIME DEFAULT NULL,
            created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_tenant_source_topic (tenant_key, source, topic),
            KEY idx_tenant_source (tenant_key, source)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_runtime_tasks (
            task_id          VARCHAR(64)  PRIMARY KEY,
            task_type        VARCHAR(64)  NOT NULL,
            stream_name      VARCHAR(64)  NOT NULL DEFAULT '',
            tenant_key       VARCHAR(128) NULL,
            session_key      VARCHAR(256) NULL,
            scope_key        VARCHAR(255) NULL,
            trace_id         VARCHAR(64)  NULL,
            service_role     VARCHAR(64)  NULL,
            status           VARCHAR(32)  NOT NULL DEFAULT 'queued',
            attempt          INT NOT NULL DEFAULT 0,
            max_attempts     INT NOT NULL DEFAULT 3,
            payload_json     MEDIUMTEXT NOT NULL,
            queue_message_id VARCHAR(128) NULL,
            last_error       TEXT NULL,
            claimed_by       VARCHAR(128) NULL,
            result_details_json MEDIUMTEXT NULL,
            created_at       DATETIME NOT NULL,
            updated_at       DATETIME NOT NULL,
            completed_at     DATETIME NULL,
            KEY idx_status     (status),
            KEY idx_tenant_key (tenant_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_agent_tool_invocations (
            invocation_id      VARCHAR(64)  NOT NULL COMMENT '工具调用唯一ID',
            tenant_key         VARCHAR(255) NOT NULL COMMENT '租户/用户标识',
            session_key        VARCHAR(255) NOT NULL COMMENT '会话标识',
            message_seq        INT          NULL COMMENT '关联 nb_session_messages.seq，可为空',
            tool_call_id       VARCHAR(128) NULL COMMENT '模型侧 tool_call_id',
            tool_name          VARCHAR(128) NOT NULL COMMENT '工具名',
            tool_category      VARCHAR(32)  NOT NULL DEFAULT 'sync_read'
                COMMENT 'sync_read/sync_write/async_task',
            execution_mode     VARCHAR(32)  NOT NULL DEFAULT 'immediate'
                COMMENT 'immediate/durable',
            status             VARCHAR(32)  NOT NULL DEFAULT 'requested'
                COMMENT 'requested/running/submitted/succeeded/failed/noop',
            input_json         JSON         NULL COMMENT '工具入参快照',
            output_summary     TEXT         NULL COMMENT '工具返回摘要，不存大结果全文',
            runtime_task_id    VARCHAR(64)  NULL COMMENT '关联 nb_runtime_tasks.task_id',
            business_ref_type  VARCHAR(64)  NULL COMMENT '业务引用类型',
            business_ref_id    VARCHAR(128) NULL COMMENT '业务引用ID',
            trace_id           VARCHAR(64)  NULL COMMENT '链路追踪ID',
            last_error         TEXT         NULL COMMENT '失败原因摘要',
            created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            completed_at       DATETIME     NULL,
            PRIMARY KEY (invocation_id),
            KEY idx_tool_invocations_tenant_time (tenant_key, created_at),
            KEY idx_tool_invocations_session_time (tenant_key, session_key, created_at),
            KEY idx_tool_invocations_tool_status (tenant_key, tool_name, status, created_at),
            KEY idx_tool_invocations_runtime_task (runtime_task_id),
            KEY idx_tool_invocations_business_ref (business_ref_type, business_ref_id),
            KEY idx_tool_invocations_trace (trace_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='Agent 工具调用链路表'
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_skin_profiles (
            profile_id           BIGINT NOT NULL AUTO_INCREMENT,
            tenant_key           VARCHAR(255) NOT NULL,
            session_key          VARCHAR(255) NOT NULL,
            message_id           VARCHAR(255) NULL,
            image_url            TEXT NULL,
            analysis_id          VARCHAR(255) NULL,
            skin_attribute_json  JSON NULL,
            overall_state        VARCHAR(64)  NULL,
            advantages_json      JSON NULL,
            signals_json         JSON NULL,
            sync_status          VARCHAR(32)  NOT NULL DEFAULT 'pending',
            sync_reason          VARCHAR(128) NULL,
            synced_to_user_doc_at DATETIME NULL,
            sync_error           TEXT NULL,
            created_at           DATETIME NOT NULL,
            updated_at           DATETIME NOT NULL,
            PRIMARY KEY (profile_id),
            KEY idx_tenant_sync (tenant_key, sync_status, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_profile_block_meta (
            meta_id         BIGINT NOT NULL AUTO_INCREMENT,
            tenant_key      VARCHAR(255) NOT NULL,
            block_name      VARCHAR(128) NOT NULL,
            last_writer     VARCHAR(64)  NULL,
            last_profile_id BIGINT NULL,
            content_hash    VARCHAR(64)  NOT NULL DEFAULT '',
            last_synced_at  DATETIME NULL,
            created_at      DATETIME NOT NULL,
            updated_at      DATETIME NOT NULL,
            PRIMARY KEY (meta_id),
            UNIQUE KEY uk_tenant_block (tenant_key, block_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_tenant_memory_events (
            id         BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            tenant_key VARCHAR(128) NOT NULL,
            session_key VARCHAR(256) NULL,
            source     VARCHAR(64)  NOT NULL COMMENT 'consolidation/structured_memory',
            event_name VARCHAR(128) NOT NULL,
            topic      VARCHAR(128) NULL,
            content    TEXT NULL,
            version_no INT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY idx_tenant_source  (tenant_key, source),
            KEY idx_tenant_created (tenant_key, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_llm_prefix_caches (
            id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
            provider            VARCHAR(32)  NOT NULL DEFAULT 'volcengine',
            lane                VARCHAR(64)  NOT NULL,
            tenant_key          VARCHAR(128) NOT NULL DEFAULT '__default__',
            session_key         VARCHAR(256) NOT NULL DEFAULT '',
            model               VARCHAR(128) NOT NULL,
            thinking_type       VARCHAR(32)  NOT NULL DEFAULT 'disabled',
            prompt_fingerprint  CHAR(64)     NOT NULL,
            tools_fingerprint   CHAR(64)     NOT NULL,
            response_id         VARCHAR(128) NOT NULL,
            expire_at           BIGINT       NULL,
            status              VARCHAR(32)  NOT NULL DEFAULT 'active',
            metadata_json       JSON         NULL,
            created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            last_used_at        DATETIME     NULL,
            UNIQUE KEY uk_prefix_cache (
                provider,
                lane,
                tenant_key,
                session_key,
                model,
                thinking_type,
                prompt_fingerprint,
                tools_fingerprint
            ),
            KEY idx_prefix_expire (status, expire_at),
            KEY idx_prefix_response (response_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_llm_session_caches (
            id                     BIGINT AUTO_INCREMENT PRIMARY KEY,
            provider               VARCHAR(32)  NOT NULL DEFAULT 'volcengine',
            lane                   VARCHAR(64)  NOT NULL,
            tenant_key             VARCHAR(128) NOT NULL DEFAULT '__default__',
            session_key            VARCHAR(256) NOT NULL DEFAULT '',
            model                  VARCHAR(128) NOT NULL,
            thinking_type          VARCHAR(32)  NOT NULL DEFAULT 'disabled',
            cache_mode             VARCHAR(32)  NOT NULL,
            prompt_fingerprint     CHAR(64)     NOT NULL,
            context_version        INT          NOT NULL DEFAULT 0,
            main_consolidated_from INT          NOT NULL DEFAULT 0,
            context_fingerprint    CHAR(64)     NULL,
            response_id            VARCHAR(128) NOT NULL,
            base_response_id       VARCHAR(128) NULL,
            turn_count             INT          NOT NULL DEFAULT 0,
            expire_at              BIGINT       NULL,
            status                 VARCHAR(32)  NOT NULL DEFAULT 'active',
            metadata_json          JSON         NULL,
            created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            last_used_at           DATETIME     NULL,
            UNIQUE KEY uk_session_cache (
                provider,
                lane,
                tenant_key,
                session_key,
                model,
                thinking_type,
                cache_mode,
                prompt_fingerprint,
                context_version
            ),
            KEY idx_session_expire (status, expire_at),
            KEY idx_session_response (response_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        # ── FlowCut 专属表 ──
        """
        CREATE TABLE IF NOT EXISTS fc_reference_video (
            id              BIGINT        NOT NULL AUTO_INCREMENT,
            tenant_key      VARCHAR(255)  NOT NULL,
            oss_key         VARCHAR(512)  NOT NULL,
            oss_url         VARCHAR(1024) NOT NULL,
            thumbnail_url   VARCHAR(1024) NULL,
            name            VARCHAR(255)  NOT NULL,
            product         VARCHAR(128)  NULL,
            duration        FLOAT         NOT NULL,
            file_size       BIGINT        NOT NULL,
            scene_data_json JSON          NULL,
            audio_oss_key   VARCHAR(512)  NULL,
            script_id       BIGINT        NULL,
            status          VARCHAR(32)   NOT NULL DEFAULT 'PROCESSING',
            created_at      DATETIME      NOT NULL,
            updated_at      DATETIME      NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_ref_tenant (tenant_key),
            KEY idx_fc_ref_status (status)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_material (
            id                  BIGINT       NOT NULL AUTO_INCREMENT,
            tenant_key          VARCHAR(255) NOT NULL,
            oss_key             VARCHAR(512) NOT NULL,
            oss_url             VARCHAR(1024) NOT NULL,
            thumbnail_url       VARCHAR(1024) NULL,
            preview_url         VARCHAR(1024) NULL,
            name                VARCHAR(255) NOT NULL,
            transcript          TEXT         NULL,
            description         TEXT         NULL,
            category            VARCHAR(32)  NOT NULL,
            product             VARCHAR(128) NULL,
            scene_role          VARCHAR(64)  NULL,
            duration            FLOAT        NOT NULL,
            file_size           BIGINT       NOT NULL,
            status              VARCHAR(16)  NOT NULL DEFAULT 'PROCESSING',
            usage_count         INT          NOT NULL DEFAULT 0,
            parent_material_id  BIGINT       NULL,
            source_video_id     BIGINT       NULL,
            vector_indexed      TINYINT(1)   NOT NULL DEFAULT 0,
            created_at          DATETIME     NOT NULL,
            updated_at          DATETIME     NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_material_tenant (tenant_key),
            KEY idx_fc_material_status (status),
            KEY idx_fc_material_category (category),
            KEY idx_fc_material_product (product),
            KEY idx_fc_material_pending_vector (vector_indexed, status)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_highlight_asset (
            id              BIGINT        NOT NULL AUTO_INCREMENT,
            tenant_key      VARCHAR(255)  NOT NULL,
            asset_type      VARCHAR(32)   NOT NULL,
            drama_name      VARCHAR(128)  NULL,
            episode_no      INT           NULL,
            connector_role  VARCHAR(64)   NULL,
            oss_key         VARCHAR(512)  NOT NULL,
            oss_url         VARCHAR(1024) NOT NULL,
            name            VARCHAR(255)  NOT NULL,
            duration        FLOAT         NOT NULL DEFAULT 0,
            file_size       BIGINT        NOT NULL,
            status          VARCHAR(16)   NOT NULL DEFAULT 'READY',
            metadata_json   JSON          NULL,
            created_at      DATETIME      NOT NULL,
            updated_at      DATETIME      NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_highlight_asset_tenant (tenant_key),
            KEY idx_fc_highlight_asset_type (asset_type),
            KEY idx_fc_highlight_asset_drama (tenant_key, drama_name),
            KEY idx_fc_highlight_asset_connector (tenant_key, connector_role)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_script (
            id                  BIGINT       NOT NULL AUTO_INCREMENT,
            tenant_key          VARCHAR(255) NOT NULL,
            source              VARCHAR(16)  NOT NULL,
            reference_video_id  BIGINT       NULL,
            product             VARCHAR(128) NULL,
            segments_json       JSON         NOT NULL,
            status              VARCHAR(16)  NOT NULL DEFAULT 'DRAFT',
            created_at          DATETIME     NOT NULL,
            updated_at          DATETIME     NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_script_tenant_status (tenant_key, status),
            KEY idx_fc_script_ref_video (reference_video_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_creative (
            id                     BIGINT       NOT NULL AUTO_INCREMENT,
            tenant_key             VARCHAR(255) NOT NULL,
            session_key            VARCHAR(255) NOT NULL,
            script_id              BIGINT       NULL,
            oss_key                VARCHAR(512) NULL,
            oss_url                VARCHAR(1024) NULL,
            srt_url                VARCHAR(1024) NULL,
            creative_type          VARCHAR(32)  NOT NULL DEFAULT 'normal',
            batch_id               VARCHAR(64)  NULL,
            source_asset_id        BIGINT       NULL,
            connector_asset_id     BIGINT       NULL,
            highlight_start        FLOAT        NULL,
            highlight_end          FLOAT        NULL,
            highlight_reason_json  JSON         NULL,
            compose_plan_json      JSON         NULL,
            clip_plan_json         JSON         NULL,
            status                 VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
            label                  VARCHAR(16)  NOT NULL DEFAULT 'NORMAL',
            qianchuan_material_id  VARCHAR(64)  NULL,
            qianchuan_campaign_id  VARCHAR(64)  NULL,
            created_at             DATETIME     NOT NULL,
            updated_at             DATETIME     NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_creative_tenant (tenant_key),
            KEY idx_fc_creative_status (status),
            KEY idx_fc_creative_batch (batch_id),
            KEY idx_fc_creative_type (creative_type)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_material_usage (
            id          BIGINT   NOT NULL AUTO_INCREMENT,
            material_id BIGINT   NOT NULL,
            creative_id BIGINT   NOT NULL,
            segment_idx INT      NOT NULL,
            created_at  DATETIME NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_usage_material (material_id),
            KEY idx_fc_usage_creative (creative_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_qianchuan_account (
            id                       BIGINT       NOT NULL AUTO_INCREMENT,
            tenant_key               VARCHAR(255) NOT NULL,
            advertiser_id            VARCHAR(64)  NOT NULL,
            access_token             TEXT         NOT NULL,
            refresh_token            TEXT         NOT NULL,
            access_token_expires_at  DATETIME     NOT NULL,
            refresh_token_expires_at DATETIME     NOT NULL,
            campaign_id              VARCHAR(64)  NULL,
            status                   VARCHAR(16)  NOT NULL DEFAULT 'active',
            created_at               DATETIME     NOT NULL,
            updated_at               DATETIME     NOT NULL,
            PRIMARY KEY (id),
            KEY idx_fc_qianchuan_tenant (tenant_key),
            UNIQUE KEY uq_fc_qianchuan_advertiser (tenant_key, advertiser_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_user (
            id            BIGINT       NOT NULL AUTO_INCREMENT,
            username      VARCHAR(128) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            tenant_key    VARCHAR(255) NOT NULL,
            display_name  VARCHAR(128) NULL,
            disabled      TINYINT      NOT NULL DEFAULT 0,
            created_at    DATETIME     NOT NULL,
            updated_at    DATETIME     NOT NULL,
            PRIMARY KEY (id),
            UNIQUE KEY uq_fc_user_username (username),
            KEY idx_fc_user_tenant (tenant_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_login_session (
            session_id_hash VARCHAR(64)  NOT NULL,
            user_id         BIGINT       NOT NULL,
            tenant_key      VARCHAR(255) NOT NULL,
            expires_at      DATETIME     NOT NULL,
            created_at      DATETIME     NOT NULL,
            PRIMARY KEY (session_id_hash),
            KEY idx_fc_login_session_user (user_id),
            KEY idx_fc_login_session_expires (expires_at)
        )
        """,
        # ── 客户端 exe 用户行为日志 ──
        """
        CREATE TABLE IF NOT EXISTS fc_client_event_log (
            id              BIGINT       NOT NULL AUTO_INCREMENT,
            event_id        VARCHAR(64)  NOT NULL,
            tenant_key      VARCHAR(255) NOT NULL DEFAULT 'flowcut',
            user_id         BIGINT       NULL,
            username        VARCHAR(128) NULL,
            session_key     VARCHAR(255) NULL,
            app_version     VARCHAR(64)  NULL,
            platform        VARCHAR(64)  NULL,
            device_id       VARCHAR(128) NULL,
            event_type      VARCHAR(128) NOT NULL,
            event_source    VARCHAR(32)  NOT NULL DEFAULT 'client',
            status          VARCHAR(32)  NOT NULL DEFAULT 'ok',
            severity        VARCHAR(16)  NOT NULL DEFAULT 'info',
            page            VARCHAR(128) NULL,
            route           VARCHAR(255) NULL,
            component       VARCHAR(128) NULL,
            action          VARCHAR(128) NULL,
            request_id      VARCHAR(64)  NULL,
            http_method     VARCHAR(16)  NULL,
            api_path        VARCHAR(255) NULL,
            http_status     INT          NULL,
            duration_ms     INT          NULL,
            batch_id        VARCHAR(64)  NULL,
            entity_type     VARCHAR(64)  NULL,
            entity_id       VARCHAR(128) NULL,
            error_code      VARCHAR(128) NULL,
            error_message   VARCHAR(512) NULL,
            payload_json    JSON         NULL,
            ip_address      VARCHAR(64)  NULL,
            user_agent      VARCHAR(512) NULL,
            occurred_at     DATETIME     NOT NULL,
            created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_fc_client_event_id (event_id),
            KEY idx_fc_client_event_tenant_time (tenant_key, occurred_at),
            KEY idx_fc_client_event_user_time (user_id, occurred_at),
            KEY idx_fc_client_event_session_time (session_key, occurred_at),
            KEY idx_fc_client_event_type_time (tenant_key, event_type, occurred_at),
            KEY idx_fc_client_event_status_time (tenant_key, status, occurred_at),
            KEY idx_fc_client_event_batch_time (tenant_key, batch_id, occurred_at),
            KEY idx_fc_client_event_request (request_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='FlowCut 客户端 exe 用户行为日志'
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_highlight_event_log (
            id                    BIGINT       NOT NULL AUTO_INCREMENT,
            event_id              VARCHAR(64)  NOT NULL,
            tenant_key            VARCHAR(255) NOT NULL DEFAULT 'flowcut',
            user_id               BIGINT       NULL,
            username              VARCHAR(128) NULL,
            session_key           VARCHAR(255) NULL,
            event_type            VARCHAR(128) NOT NULL,
            event_source          VARCHAR(32)  NOT NULL DEFAULT 'api',
            status                VARCHAR(32)  NOT NULL DEFAULT 'ok',
            severity              VARCHAR(16)  NOT NULL DEFAULT 'info',
            batch_id              VARCHAR(64)  NULL,
            stage                 VARCHAR(32)  NULL,
            runtime_task_id       VARCHAR(128) NULL,
            tool_invocation_id    VARCHAR(64)  NULL,
            entity_type           VARCHAR(64)  NULL,
            entity_id             VARCHAR(128) NULL,
            drama_name            VARCHAR(255) NULL,
            episode_no            INT          NULL,
            candidate_idx         INT          NULL,
            request_id            VARCHAR(64)  NULL,
            http_method           VARCHAR(16)  NULL,
            api_path              VARCHAR(255) NULL,
            http_status           INT          NULL,
            duration_ms           INT          NULL,
            external_provider     VARCHAR(64)  NULL,
            external_base_host    VARCHAR(128) NULL,
            external_model        VARCHAR(128) NULL,
            external_operation    VARCHAR(128) NULL,
            external_http_status  INT          NULL,
            error_code            VARCHAR(128) NULL,
            error_message         VARCHAR(512) NULL,
            context_json          JSON         NULL,
            occurred_at           DATETIME     NOT NULL,
            created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_fc_highlight_event_id (event_id),
            KEY idx_fc_hl_event_tenant_time (tenant_key, occurred_at),
            KEY idx_fc_hl_event_user_time (user_id, occurred_at),
            KEY idx_fc_hl_event_batch_time (tenant_key, batch_id, occurred_at),
            KEY idx_fc_hl_event_stage_time (tenant_key, batch_id, stage, occurred_at),
            KEY idx_fc_hl_event_runtime_task (runtime_task_id),
            KEY idx_fc_hl_event_tool_invocation (tool_invocation_id),
            KEY idx_fc_hl_event_type_time (tenant_key, event_type, occurred_at),
            KEY idx_fc_hl_event_status_time (tenant_key, status, occurred_at),
            KEY idx_fc_hl_event_external (tenant_key, external_provider, external_base_host, occurred_at),
            KEY idx_fc_hl_event_entity (entity_type, entity_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        COMMENT='跨集高光任务链路事件日志'
        """,
        # ── 跨集高光批量管道 ──
        """
        CREATE TABLE IF NOT EXISTS fc_highlight_batch (
            id              BIGINT       NOT NULL AUTO_INCREMENT,
            batch_id        VARCHAR(64)  NOT NULL,
            tenant_key      VARCHAR(255) NOT NULL DEFAULT 'flowcut',
            drama_name      VARCHAR(255) NOT NULL,
            num_candidates  INT          NOT NULL DEFAULT 3,
            status          VARCHAR(32)  NOT NULL DEFAULT 'EPISODE_PREP',
            orchestrator_state_json JSON NULL,
            summary_json    JSON         NULL,
            merged_shots_json LONGTEXT   NULL,
            created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY uq_fc_highlight_batch (batch_id),
            KEY idx_fc_highlight_batch_status (status),
            KEY idx_fc_highlight_batch_drama (drama_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fc_highlight_stage (
            id              BIGINT       NOT NULL AUTO_INCREMENT,
            batch_id        VARCHAR(64)  NOT NULL,
            stage           VARCHAR(32)  NOT NULL,
            episode_no      INT          NULL,
            candidate_idx   INT          NULL,
            creative_id     BIGINT       NULL,
            runtime_task_id VARCHAR(128) NULL,
            status          VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
            input_json      JSON         NULL,
            result_json     JSON         NULL,
            error           TEXT         NULL,
            started_at      DATETIME     NULL,
            completed_at    DATETIME     NULL,
            created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY idx_fc_highlight_stage_batch (batch_id),
            KEY idx_fc_highlight_stage_status (batch_id, stage, status)
        )
        """,
    ]
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            # ── 迁移：若 nb_cron_jobs 是旧 schema（主键为 job_id），则重建 ──
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_cron_jobs'
                  AND COLUMN_NAME  = 'job_id'
                """
            )
            row = await cur.fetchone()
            if row and row[0] > 0:
                await cur.execute("DROP TABLE nb_cron_jobs")

            for sql in statements:
                await cur.execute(sql)

            # 迁移：补充日志排障常用索引。均为普通索引，不建立外键。
            _extra_indexes: list[tuple[str, str]] = [
                (
                    "fc_highlight_batch",
                    "KEY idx_fc_highlight_batch_tenant_time (tenant_key, created_at)",
                ),
                (
                    "fc_highlight_batch",
                    "KEY idx_fc_highlight_batch_tenant_status_time (tenant_key, status, updated_at)",
                ),
                (
                    "fc_highlight_stage",
                    "KEY idx_fc_highlight_stage_runtime_task (runtime_task_id)",
                ),
                (
                    "fc_highlight_stage",
                    "KEY idx_fc_highlight_stage_creative (creative_id)",
                ),
                (
                    "nb_runtime_tasks",
                    "KEY idx_nb_runtime_tasks_tenant_status_time (tenant_key, status, updated_at)",
                ),
            ]
            for table, definition in _extra_indexes:
                try:
                    await cur.execute(f"ALTER TABLE {table} ADD {definition}")
                except Exception:
                    pass

            # 迁移：给 nb_runtime_tasks 补 scope_key（用于展示/排障）
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_runtime_tasks'
                  AND COLUMN_NAME  = 'scope_key'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    """
                    ALTER TABLE nb_runtime_tasks
                    ADD COLUMN scope_key VARCHAR(255) NULL AFTER session_key
                    """
                )

            # 迁移：给 nb_runtime_tasks 补 result_details_json（持久化 TaskExecutionResult.details）
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_runtime_tasks'
                  AND COLUMN_NAME  = 'result_details_json'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    """
                    ALTER TABLE nb_runtime_tasks
                    ADD COLUMN result_details_json MEDIUMTEXT NULL AFTER claimed_by
                    """
                )

            # 迁移：兜底补齐 nb_deep_analysis_reports / nb_agent_field_reports 缺失字段
            _deep_report_columns: list[tuple[str, str, str]] = [
                ("nb_deep_analysis_reports", "strategy_version", "VARCHAR(32) NULL AFTER status"),
                ("nb_deep_analysis_reports", "trace_id",         "VARCHAR(64) NULL AFTER strategy_version"),
                ("nb_deep_analysis_reports", "overview_json",    "MEDIUMTEXT NULL AFTER trace_id"),
                ("nb_deep_analysis_reports", "decode_json",      "MEDIUMTEXT NULL AFTER overview_json"),
                ("nb_deep_analysis_reports", "secret_json",      "MEDIUMTEXT NULL AFTER decode_json"),
                ("nb_deep_analysis_reports", "track_json",       "MEDIUMTEXT NULL AFTER secret_json"),
                ("nb_agent_field_reports",   "trace_id",         "VARCHAR(64) NULL AFTER status"),
                ("nb_agent_field_reports",   "overview_json",    "MEDIUMTEXT NULL AFTER trace_id"),
                ("nb_agent_field_reports",   "decode_json",      "MEDIUMTEXT NULL AFTER overview_json"),
                ("nb_agent_field_reports",   "secret_json",      "MEDIUMTEXT NULL AFTER decode_json"),
                ("nb_agent_field_reports",   "track_json",       "MEDIUMTEXT NULL AFTER secret_json"),
            ]
            for table, column, definition in _deep_report_columns:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = %s
                      AND COLUMN_NAME  = %s
                    """,
                    (table, column),
                )
                row = await cur.fetchone()
                if row and row[0] == 0:
                    await cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

            # 迁移：fc_reference_video.status 列宽扩到 VARCHAR(32)，
            # 旧 schema 为 VARCHAR(16)，写入 'AWAITING_CLASSIFICATION'(22) 会触发 1406 DataError。
            await cur.execute(
                """
                SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_reference_video'
                  AND COLUMN_NAME  = 'status'
                """
            )
            row = await cur.fetchone()
            if row and row[0] is not None and row[0] < 32:
                await cur.execute(
                    "ALTER TABLE fc_reference_video MODIFY COLUMN status VARCHAR(32) NOT NULL DEFAULT 'PROCESSING'"
                )

            # 迁移：fc_material 新增 parent_material_id（2026-05-15）
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_material'
                  AND COLUMN_NAME  = 'parent_material_id'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    "ALTER TABLE fc_material ADD COLUMN parent_material_id BIGINT NULL AFTER usage_count"
                )

            # 迁移：fc_material 新增 description / source_video_id / product / scene_role / vector_indexed（2026-05-18）
            _material_columns: list[tuple[str, str]] = [
                ("description",       "TEXT NULL AFTER transcript"),
                ("source_video_id",   "BIGINT NULL AFTER parent_material_id"),
                ("product",           "VARCHAR(128) NULL AFTER category"),
                ("scene_role",        "VARCHAR(64) NULL AFTER product"),
                ("vector_indexed",    "TINYINT(1) NOT NULL DEFAULT 0 AFTER source_video_id"),
                ("keyword_vec_idx",   "KEY idx_fc_material_product (product)"),
                ("pending_vec_idx",   "KEY idx_fc_material_pending_vector (vector_indexed, status)"),
            ]
            for column, definition in _material_columns:
                if definition.startswith("KEY "):
                    # index — CREATE INDEX IF NOT EXISTS 不适用，用 try-except
                    try:
                        await cur.execute(f"ALTER TABLE fc_material ADD {definition}")
                    except Exception:
                        pass
                else:
                    await cur.execute(
                        """
                        SELECT COUNT(*) FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE()
                          AND TABLE_NAME   = 'fc_material'
                          AND COLUMN_NAME  = %s
                        """,
                        (column,),
                    )
                    col_row = await cur.fetchone()
                    if col_row and col_row[0] == 0:
                        await cur.execute(f"ALTER TABLE fc_material ADD COLUMN {column} {definition}")

            # 迁移（2026-05-23）：fc_script 新增 product 列。
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_script'
                  AND COLUMN_NAME  = 'product'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    "ALTER TABLE fc_script "
                    "ADD COLUMN product VARCHAR(128) NULL AFTER reference_video_id"
                )

            # 迁移（2026-05-23）：把旧流程的孤儿 ref_video 状态置为 FAILED。
            # 旧 AWAITING_CLASSIFICATION / DECOMPOSED 记录的 script_id 必然为 NULL
            # （旧流程只在 clip_create 后才生成 fc_script），新流程不再支持这两个状态。
            await cur.execute(
                """
                UPDATE fc_reference_video
                   SET status='FAILED'
                 WHERE status IN ('AWAITING_CLASSIFICATION','DECOMPOSED')
                   AND script_id IS NULL
                """
            )

            # 迁移（2026-05-28）：fc_creative 加千川数据回流字段 qc_*
            _qc_creative_columns: list[tuple[str, str]] = [
                ("qc_material_id",  "VARCHAR(64) NULL AFTER qianchuan_campaign_id"),
                ("qc_cost",         "DECIMAL(12,4) NULL AFTER qc_material_id"),
                ("qc_impressions",  "BIGINT NULL AFTER qc_cost"),
                ("qc_clicks",       "BIGINT NULL AFTER qc_impressions"),
                ("qc_conversions",  "BIGINT NULL AFTER qc_clicks"),
                ("qc_synced_at",    "DATETIME NULL AFTER qc_conversions"),
            ]
            for column, definition in _qc_creative_columns:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'fc_creative'
                      AND COLUMN_NAME  = %s
                    """,
                    (column,),
                )
                col_row = await cur.fetchone()
                if col_row and col_row[0] == 0:
                    await cur.execute(
                        f"ALTER TABLE fc_creative ADD COLUMN {column} {definition}"
                    )

            # 迁移（2026-06-12）：fc_creative 加高光批量产物字段。
            _highlight_creative_columns: list[tuple[str, str]] = [
                ("creative_type",         "VARCHAR(32) NOT NULL DEFAULT 'normal' AFTER srt_url"),
                ("batch_id",              "VARCHAR(64) NULL AFTER creative_type"),
                ("source_asset_id",       "BIGINT NULL AFTER batch_id"),
                ("connector_asset_id",    "BIGINT NULL AFTER source_asset_id"),
                ("highlight_start",       "FLOAT NULL AFTER connector_asset_id"),
                ("highlight_end",         "FLOAT NULL AFTER highlight_start"),
                ("highlight_reason_json", "JSON NULL AFTER highlight_end"),
                ("compose_plan_json",     "JSON NULL AFTER highlight_reason_json"),
                ("clip_plan_json",        "JSON NULL AFTER compose_plan_json"),
            ]
            for column, definition in _highlight_creative_columns:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'fc_creative'
                      AND COLUMN_NAME  = %s
                    """,
                    (column,),
                )
                col_row = await cur.fetchone()
                if col_row and col_row[0] == 0:
                    await cur.execute(f"ALTER TABLE fc_creative ADD COLUMN {column} {definition}")

            for index_name, definition in [
                ("idx_fc_creative_batch", "KEY idx_fc_creative_batch (batch_id)"),
                ("idx_fc_creative_type", "KEY idx_fc_creative_type (creative_type)"),
            ]:
                try:
                    await cur.execute(f"ALTER TABLE fc_creative ADD {definition}")
                except Exception:
                    pass

            # 迁移（2026-06-25）：fc_creative 加前贴字段
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_creative'
                  AND COLUMN_NAME  = 'preroll_asset_id'
                """,
            )
            _pr_row = await cur.fetchone()
            if _pr_row and _pr_row[0] == 0:
                await cur.execute(
                    "ALTER TABLE fc_creative"
                    " ADD COLUMN preroll_asset_id BIGINT NULL AFTER connector_asset_id"
                )

            # 迁移（2026-05-28）：新建 fc_qianchuan_orphan 表
            # 存放千川侧有消耗但本地无法匹配到 fc_creative 的 material_id 记录
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fc_qianchuan_orphan (
                    id              BIGINT       NOT NULL AUTO_INCREMENT,
                    tenant_key      VARCHAR(255) NOT NULL,
                    qc_material_id  VARCHAR(64)  NOT NULL,
                    material_name   VARCHAR(512) NULL,
                    qc_cost         DECIMAL(12,4) NULL,
                    qc_conversions  BIGINT       NULL,
                    raw_json        JSON         NULL,
                    synced_at       DATETIME     NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uq_fc_orphan_material (tenant_key, qc_material_id),
                    KEY idx_fc_orphan_tenant (tenant_key)
                )
                """
            )


class Database:
    """对 aiomysql 连接池的轻量封装。

    用法示例：
        db = Database(host=..., port=..., user=..., password=..., db=...)
        await db.connect()                      # 启动时调用一次
        async with db.acquire() as conn:        # 在各仓库中使用
            async with conn.cursor() as cur:
                await cur.execute(...)
        await db.close()                        # 关闭时调用
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        db: str,
        charset: str = "utf8mb4",
        minsize: int = 1,
        maxsize: int = 10,
        connect_timeout: float = 5,
    ) -> None:
        self._kwargs = dict(
            host=host,
            port=port,
            user=user,
            password=password,
            db=db,
            charset=charset,
            minsize=minsize,
            maxsize=maxsize,
            connect_timeout=connect_timeout,
            autocommit=True,
            pool_recycle=300,  # 5 分钟回收，需短于云 MySQL wait_timeout（通常 600s）
        )
        self._pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(**self._kwargs)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[aiomysql.Connection, None]:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called")
        conn = await self._acquire_with_retry()
        try:
            await conn.ping(reconnect=True)
            yield conn
        finally:
            self._pool.release(conn)

    async def _acquire_with_retry(self, max_retries: int = 3) -> aiomysql.Connection:
        """获取连接，网络瞬断时重试。"""
        import asyncio
        last_err = None
        for attempt in range(max_retries):
            try:
                return await self._pool.acquire()
            except Exception as exc:
                last_err = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise last_err  # type: ignore[misc]
