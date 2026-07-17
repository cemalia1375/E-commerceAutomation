"""Mojing 的异步 MySQL 连接池。"""

from __future__ import annotations

import aiomysql


async def ensure_schema(db: "Database") -> None:
    """建表（IF NOT EXISTS），安全幂等。"""
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
        CREATE TABLE IF NOT EXISTS nb_tenant_action_usage (
            tenant_key       VARCHAR(255) NOT NULL,
            action_key       VARCHAR(128) NOT NULL,
            submitted_count  INT UNSIGNED NOT NULL DEFAULT 0,
            succeeded_count  INT UNSIGNED NOT NULL DEFAULT 0,
            failed_count     INT UNSIGNED NOT NULL DEFAULT 0,
            created_at       DATETIME NOT NULL,
            updated_at       DATETIME NOT NULL,
            PRIMARY KEY (tenant_key, action_key)
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
            source_task_id VARCHAR(64)  NULL,
            session_key    VARCHAR(256) NULL,
            trace_id       VARCHAR(64)  NULL,
            message_seq_start INT NULL,
            message_seq_end   INT NULL,
            operator_id    VARCHAR(255) NULL,
            created_at     DATETIME NOT NULL,
            PRIMARY KEY (version_id),
            KEY idx_doc_versions        (doc_id, version_no),
            KEY idx_doc_versions_tenant (tenant_key, doc_type, doc_name, version_no),
            KEY idx_doc_versions_source_task (source_task_id),
            KEY idx_doc_versions_session (tenant_key, session_key, created_at)
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
        CREATE TABLE IF NOT EXISTS nb_skincare_cabinet_product (
            id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '产品ID',
            user_id            VARCHAR(255) NOT NULL COMMENT '所属用户ID',
            brand              VARCHAR(100) NOT NULL DEFAULT '' COMMENT '产品品牌',
            product_name       VARCHAR(200) NOT NULL DEFAULT '' COMMENT '产品名称',
            category           VARCHAR(50) NOT NULL DEFAULT '' COMMENT '产品类别',
            core_efficacy      JSON NULL COMMENT '核心功效',
            core_ingredients   JSON NULL COMMENT '核心成分',
            risk_ingredients   JSON NULL COMMENT '风险成分',
            commercial_image   VARCHAR(500) NOT NULL DEFAULT '' COMMENT '商业白底图URL',
            expiration_date    DATE NULL COMMENT '未开封保质期截止日',
            storage_conditions VARCHAR(200) NOT NULL DEFAULT '' COMMENT '储存条件',
            specifications     VARCHAR(100) NOT NULL DEFAULT '' COMMENT '规格/容量',
            user_photo         VARCHAR(500) NOT NULL DEFAULT '' COMMENT '用户拍摄的产品照片URL',
            in_cabinet         TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否记入护肤柜（0否 1是）',
            usage_status       VARCHAR(20) NOT NULL DEFAULT 'using' COMMENT '使用状态：using/unopened/finished',
            opened_date        DATE NULL COMMENT '开封日期',
            opened_expiry      DATE NULL COMMENT '开封后建议用完日期',
            creator            VARCHAR(64) NOT NULL DEFAULT '' COMMENT '创建者',
            create_time        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
            updater            VARCHAR(64) NOT NULL DEFAULT '' COMMENT '更新者',
            update_time        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
            deleted            TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否删除',
            PRIMARY KEY (id),
            KEY idx_cabinet_user_status (user_id, in_cabinet, deleted, update_time),
            KEY idx_cabinet_user_product (user_id, brand, product_name, in_cabinet, deleted)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='护肤柜-用户护肤品资产表'
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
        CREATE TABLE IF NOT EXISTS nb_memory_entries (
            id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
            tenant_key         VARCHAR(128) NOT NULL,
            source             VARCHAR(64)  NOT NULL DEFAULT 'main',
            memory_type        VARCHAR(32)  NOT NULL DEFAULT 'chitchat',
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
        CREATE TABLE IF NOT EXISTS nb_memory_ledgers (
            ledger_id              VARCHAR(64)  NOT NULL PRIMARY KEY,
            tenant_key             VARCHAR(128) NOT NULL,
            session_key            VARCHAR(256) NOT NULL,
            source                 VARCHAR(64)  NOT NULL DEFAULT 'main',
            trigger_type           VARCHAR(64)  NOT NULL DEFAULT 'context_compression',
            status                 VARCHAR(32)  NOT NULL DEFAULT 'queued',
            runtime_task_id        VARCHAR(64)  NULL,
            trace_id               VARCHAR(64)  NULL,

            message_seq_start      INT          NULL,
            message_seq_end        INT          NULL,
            last_consolidated_from INT          NULL,
            last_consolidated_to   INT          NULL,
            dropped_count          INT          NOT NULL DEFAULT 0,
            tokens_before          INT          NULL,
            tokens_after           INT          NULL,
            source_chunk_hash      CHAR(64)     NULL,

            source_chunk_json      MEDIUMTEXT   NULL,
            memory_before_json     MEDIUMTEXT   NULL,
            memory_actions_json    MEDIUMTEXT   NULL,
            memory_after_json      MEDIUMTEXT   NULL,
            business_snapshot_json MEDIUMTEXT   NULL,

            dream_status           VARCHAR(32)  NOT NULL DEFAULT 'pending',
            last_error             TEXT         NULL,
            metadata_json          JSON         NULL,
            created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            completed_at           DATETIME     NULL,

            KEY idx_memory_ledger_tenant_source (tenant_key, source, created_at),
            KEY idx_memory_ledger_session (tenant_key, session_key, created_at),
            KEY idx_memory_ledger_status (status, dream_status, created_at),
            KEY idx_memory_ledger_task (runtime_task_id),
            KEY idx_memory_ledger_trace (trace_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_subagent_runs (
            run_id                    VARCHAR(64)  NOT NULL PRIMARY KEY,
            tenant_key                VARCHAR(128) NOT NULL,
            session_key               VARCHAR(256) NOT NULL,
            subagent_name             VARCHAR(64)  NOT NULL,
            run_mode                  VARCHAR(32)  NOT NULL,
            status                    VARCHAR(32)  NOT NULL DEFAULT 'candidate',

            owner_type                VARCHAR(64)  NOT NULL DEFAULT 'manual',
            owner_id                  VARCHAR(128) NULL,
            runtime_task_id           VARCHAR(64)  NULL,
            trace_id                  VARCHAR(64)  NULL,

            objective                 TEXT         NULL,
            input_refs_json           JSON         NULL,
            payload_json              JSON         NULL,
            permission_profile_json   JSON         NULL,
            expected_artifacts_json   JSON         NULL,

            summary                   TEXT         NULL,
            reply_text                MEDIUMTEXT   NULL,
            last_error                TEXT         NULL,
            metadata_json             JSON         NULL,

            created_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at                DATETIME     NULL,
            completed_at              DATETIME     NULL,
            updated_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

            KEY idx_subagent_runs_tenant_session (tenant_key, session_key, created_at),
            KEY idx_subagent_runs_subagent (tenant_key, subagent_name, status, created_at),
            KEY idx_subagent_runs_owner (owner_type, owner_id),
            KEY idx_subagent_runs_runtime_task (runtime_task_id),
            KEY idx_subagent_runs_trace (trace_id),
            KEY idx_subagent_runs_status (status, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_subagent_artifacts (
            artifact_id               VARCHAR(64)  NOT NULL PRIMARY KEY,
            run_id                    VARCHAR(64)  NOT NULL,
            tenant_key                VARCHAR(128) NOT NULL,
            session_key               VARCHAR(256) NOT NULL,

            artifact_type             VARCHAR(64)  NOT NULL,
            status                    VARCHAR(32)  NOT NULL DEFAULT 'draft',

            owner_type                VARCHAR(64)  NOT NULL DEFAULT 'manual',
            owner_id                  VARCHAR(128) NULL,
            artifact_key              VARCHAR(128) NULL,

            content                   MEDIUMTEXT   NOT NULL,
            source_refs_json          JSON         NULL,
            metadata_json             JSON         NULL,

            created_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            applied_at                DATETIME     NULL,

            KEY idx_subagent_artifacts_run (run_id),
            KEY idx_subagent_artifacts_tenant_session (tenant_key, session_key, created_at),
            KEY idx_subagent_artifacts_type (tenant_key, artifact_type, status, created_at),
            KEY idx_subagent_artifacts_owner (owner_type, owner_id),
            KEY idx_subagent_artifacts_status (status, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_agent_obligations (
            obligation_id      VARCHAR(64) PRIMARY KEY,
            tenant_key         VARCHAR(128) NOT NULL,
            session_key        VARCHAR(256) NULL,
            status             VARCHAR(32) NOT NULL DEFAULT 'pending'
                COMMENT 'pending/dispatched/cancelled',
            action_type        VARCHAR(64) NOT NULL,
            dependency_type    VARCHAR(64) NULL,
            payload_json       JSON NULL,
            evidence_json      JSON NULL,
            dispatched_task_id VARCHAR(64) NULL,
            dedupe_key         VARCHAR(128) NULL,
            created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_obligation_dedupe (tenant_key, dedupe_key),
            KEY idx_obligation_pending (tenant_key, status, dependency_type),
            KEY idx_obligation_dispatched_task (dispatched_task_id)
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
            tool_name        VARCHAR(128) NULL,
            status           VARCHAR(32)  NOT NULL DEFAULT 'queued',
            attempt          INT NOT NULL DEFAULT 0,
            max_attempts     INT NOT NULL DEFAULT 3,
            payload_json     MEDIUMTEXT NOT NULL,
            output_json      MEDIUMTEXT NULL,
            queue_message_id VARCHAR(128) NULL,
            external_job_id  VARCHAR(255) NULL,
            business_ref_type VARCHAR(64) NULL,
            business_ref_id  VARCHAR(128) NULL,
            summary          TEXT NULL,
            last_error       TEXT NULL,
            claimed_by       VARCHAR(128) NULL,
            created_at       DATETIME NOT NULL,
            updated_at       DATETIME NOT NULL,
            completed_at     DATETIME NULL,
            KEY idx_status     (status),
            KEY idx_tenant_key (tenant_key),
            KEY idx_runtime_task_external (external_job_id),
            KEY idx_runtime_task_business_ref (business_ref_type, business_ref_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nb_runtime_completion_events (
            event_id          VARCHAR(64) PRIMARY KEY,
            tenant_key        VARCHAR(128) NOT NULL,
            session_key       VARCHAR(256) NOT NULL,
            task_id           VARCHAR(64) NOT NULL,
            task_type         VARCHAR(64) NULL,
            activation_kind   VARCHAR(64) NOT NULL,
            status            VARCHAR(32) NOT NULL DEFAULT 'pending'
                COMMENT 'pending/activated/provider_consumed/expired/failed',
            source_session_key VARCHAR(256) NULL,
            business_ref_type VARCHAR(64) NULL,
            business_ref_id   VARCHAR(128) NULL,
            summary           TEXT NULL,
            reminder_text     TEXT NULL,
            dedupe_key        VARCHAR(160) NOT NULL,
            payload_json      JSON NULL,
            activation_ingress_id VARCHAR(64) NULL,
            consumed_by       VARCHAR(32) NULL,
            consumed_at       DATETIME NULL,
            created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_completion_event_dedupe (tenant_key, dedupe_key),
            KEY idx_completion_pending (tenant_key, session_key, status, created_at),
            KEY idx_completion_task (task_id),
            KEY idx_completion_kind (tenant_key, activation_kind, status)
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
                COMMENT 'requested/running/submitted/succeeded/failed/blocked/deduped',
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

            # 迁移：补齐 runtime task 生命周期可视化字段，幂等，不删除已有数据。
            _runtime_task_columns: list[tuple[str, str]] = [
                ("scope_key", "VARCHAR(255) NULL AFTER session_key"),
                ("tool_name", "VARCHAR(128) NULL AFTER service_role"),
                ("output_json", "MEDIUMTEXT NULL AFTER payload_json"),
                ("external_job_id", "VARCHAR(255) NULL AFTER queue_message_id"),
                ("business_ref_type", "VARCHAR(64) NULL AFTER external_job_id"),
                ("business_ref_id", "VARCHAR(128) NULL AFTER business_ref_type"),
                ("summary", "TEXT NULL AFTER business_ref_id"),
            ]
            for column, definition in _runtime_task_columns:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'nb_runtime_tasks'
                      AND COLUMN_NAME  = %s
                    """,
                    (column,),
                )
                row = await cur.fetchone()
                if row and row[0] == 0:
                    await cur.execute(f"ALTER TABLE nb_runtime_tasks ADD COLUMN {column} {definition}")

            # 迁移：兜底补齐 nb_deep_analysis_reports / nb_agent_field_reports 缺失字段
            # （沿用 nb_runtime_tasks.scope_key 同款 information_schema 检查模式，幂等）
            # 当前 DB 已与 docs/深度报告模块/深度报告模块文档.md §2 对齐，循环体一般不会触发；
            # 仅在外部 schema 漂移时兜底，不允许 DROP 已有数据。
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

            # 迁移：memory_entries 增加 memory_type 列（皮肤趋势记忆置顶 + dream apply 授权按此判定）。
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_memory_entries'
                  AND COLUMN_NAME  = 'memory_type'
                """,
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    "ALTER TABLE nb_memory_entries "
                    "ADD COLUMN memory_type VARCHAR(32) NOT NULL DEFAULT 'chitchat' AFTER source"
                )

            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_memory_entries'
                  AND INDEX_NAME   = 'idx_memory_tenant_source_type'
                """,
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    "ALTER TABLE nb_memory_entries "
                    "ADD KEY idx_memory_tenant_source_type (tenant_key, source, memory_type)"
                )

            # 迁移：文档版本表补齐来源追踪字段，供 dream / memory ledger 追溯长期文档副作用。
            _document_version_columns: list[tuple[str, str]] = [
                ("source_task_id", "VARCHAR(64) NULL AFTER change_source"),
                ("session_key", "VARCHAR(256) NULL AFTER source_task_id"),
                ("trace_id", "VARCHAR(64) NULL AFTER session_key"),
                ("message_seq_start", "INT NULL AFTER trace_id"),
                ("message_seq_end", "INT NULL AFTER message_seq_start"),
            ]
            for column, definition in _document_version_columns:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'nb_tenant_document_versions'
                      AND COLUMN_NAME  = %s
                    """,
                    (column,),
                )
                row = await cur.fetchone()
                if row and row[0] == 0:
                    await cur.execute(
                        f"ALTER TABLE nb_tenant_document_versions ADD COLUMN {column} {definition}"
                    )

            _document_version_indexes: list[tuple[str, str]] = [
                (
                    "idx_doc_versions_source_task",
                    "ALTER TABLE nb_tenant_document_versions ADD KEY idx_doc_versions_source_task (source_task_id)",
                ),
                (
                    "idx_doc_versions_session",
                    "ALTER TABLE nb_tenant_document_versions ADD KEY idx_doc_versions_session (tenant_key, session_key, created_at)",
                ),
            ]
            for index_name, ddl in _document_version_indexes:
                await cur.execute(
                    """
                    SELECT COUNT(*) FROM information_schema.STATISTICS
                    WHERE TABLE_SCHEMA = DATABASE()
                      AND TABLE_NAME   = 'nb_tenant_document_versions'
                      AND INDEX_NAME   = %s
                    """,
                    (index_name,),
                )
                row = await cur.fetchone()
                if row and row[0] == 0:
                    await cur.execute(ddl)

            # 迁移：护肤柜 user_id 从数值型切到字符串 tenant/user id，幂等。
            await cur.execute(
                """
                SELECT DATA_TYPE, COLUMN_TYPE
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'nb_skincare_cabinet_product'
                  AND COLUMN_NAME  = 'user_id'
                """
            )
            row = await cur.fetchone()
            if row:
                data_type = str(row[0] or "").strip().lower()
                column_type = str(row[1] or "").strip().lower()
                if data_type != "varchar" or "varchar(255)" not in column_type:
                    await cur.execute(
                        """
                        ALTER TABLE nb_skincare_cabinet_product
                        MODIFY COLUMN user_id VARCHAR(255) NOT NULL COMMENT '所属用户ID'
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
        pool_recycle: int = 3600,
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
            pool_recycle=pool_recycle,
        )
        self._pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(**self._kwargs)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    def acquire(self) -> aiomysql.pool._PoolConnectionContextManager:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._pool.acquire()
