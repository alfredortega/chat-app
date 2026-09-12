-- MySQL schema migration: align the live MySQL database (MYSQL5_1028884_ortega)
-- with the SQLAlchemy models in database.py.
--
-- The app switched to this MySQL instance after a code update, but the schema
-- lagged behind the models, so it was silently falling back to a local SQLite
-- database:
--   1. `folders` was missing the project columns added by the C07 feature.
--   2. All tables were ENGINE=MyISAM, which ignores FOREIGN KEY / ON DELETE
--      CASCADE, leaving orphaned rows (messages, linked_folders) behind.
--
-- This script is idempotent and safe to re-run. Run against the target database,
-- e.g.:
--   mysql -h mysql503.discountasp.net -u ortegadba -p MYSQL5_1028884_ortega < scripts/migrate_mysql_schema.sql
--
-- Safety: ALWAYS take a backup before running a migration against production.

SET @db = DATABASE();
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ── 1. Remove orphaned rows before real FOREIGN KEY constraints exist ─────────
-- (These rows were created because MyISAM never cascaded conversation deletes.)
DELETE m FROM messages m
LEFT JOIN conversations c ON m.conversation_id = c.id
WHERE c.id IS NULL;

DELETE lf FROM linked_folders lf
LEFT JOIN conversations c ON lf.conversation_id = c.id
WHERE c.id IS NULL;

-- ── 2. Add the C07 project columns to `folders` if missing ────────────────────
-- MySQL 5.7 has no ADD COLUMN IF NOT EXISTS, so use information_schema + dynamic SQL.
DROP PROCEDURE IF EXISTS migrate_add_column_if_missing;
DELIMITER $$
CREATE PROCEDURE migrate_add_column_if_missing(
    IN p_table VARCHAR(64),
    IN p_column VARCHAR(64),
    IN p_ddl VARCHAR(512))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = @db AND TABLE_NAME = p_table AND COLUMN_NAME = p_column
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', p_table, '` ADD COLUMN `', p_column, '` ', p_ddl);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL migrate_add_column_if_missing('folders', 'kind', "VARCHAR(50) NOT NULL DEFAULT 'folder'");
CALL migrate_add_column_if_missing('folders', 'workspace_dir', "VARCHAR(512) NOT NULL DEFAULT ''");
CALL migrate_add_column_if_missing('folders', 'template_id', "VARCHAR(100) NOT NULL DEFAULT ''");
CALL migrate_add_column_if_missing('folders', 'propagation_mode', "VARCHAR(50) NOT NULL DEFAULT 'off'");
CALL migrate_add_column_if_missing('folders', 'next_req_seq', "INTEGER NOT NULL DEFAULT 1");
CALL migrate_add_column_if_missing('folders', 'token_budget', "INTEGER NOT NULL DEFAULT 0");
CALL migrate_add_column_if_missing('folders', 'ba_conversation_id', "INTEGER NULL");

DROP PROCEDURE IF EXISTS migrate_add_column_if_missing;

-- ── 3. Convert every table to InnoDB (FOREIGN KEY + ON DELETE CASCADE) ────────
ALTER TABLE folders            ENGINE = InnoDB;
ALTER TABLE personas           ENGINE = InnoDB;
ALTER TABLE endpoints          ENGINE = InnoDB;
ALTER TABLE conversations      ENGINE = InnoDB;
ALTER TABLE messages           ENGINE = InnoDB;
ALTER TABLE settings           ENGINE = InnoDB;
ALTER TABLE research_sources   ENGINE = InnoDB;
ALTER TABLE conv_files         ENGINE = InnoDB;
ALTER TABLE linked_folders     ENGINE = InnoDB;
ALTER TABLE artifacts          ENGINE = InnoDB;
ALTER TABLE artifact_deps      ENGINE = InnoDB;
ALTER TABLE artifact_traces    ENGINE = InnoDB;
ALTER TABLE change_events      ENGINE = InnoDB;
ALTER TABLE propagation_jobs   ENGINE = InnoDB;
ALTER TABLE agent_issues       ENGINE = InnoDB;
ALTER TABLE artifact_assumptions ENGINE = InnoDB;
ALTER TABLE artifact_requests  ENGINE = InnoDB;

-- ── 4. Add FOREIGN KEY / ON DELETE constraints to match the models ─────────────
DROP PROCEDURE IF EXISTS migrate_add_fk_if_missing;
DELIMITER $$
CREATE PROCEDURE migrate_add_fk_if_missing(
    IN p_table VARCHAR(64),
    IN p_name VARCHAR(64),
    IN p_spec VARCHAR(512))
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS
        WHERE CONSTRAINT_SCHEMA = @db AND CONSTRAINT_NAME = p_name AND TABLE_NAME = p_table
    ) THEN
        SET @sql = CONCAT('ALTER TABLE `', p_table, '` ADD CONSTRAINT `', p_name, '` ', p_spec);
        PREPARE stmt FROM @sql;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL migrate_add_fk_if_missing('conversations', 'fk_conversations_persona_id',        'FOREIGN KEY (persona_id)  REFERENCES personas (id)   ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('conversations', 'fk_conversations_endpoint_id',       'FOREIGN KEY (endpoint_id) REFERENCES endpoints (id)  ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('conversations', 'fk_conversations_folder_id',         'FOREIGN KEY (folder_id)   REFERENCES folders (id)    ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('messages',      'fk_messages_conversation_id',        'FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('conv_files',    'fk_conv_files_conversation_id',      'FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('linked_folders','fk_linked_folders_conversation_id',  'FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('artifacts',     'fk_artifacts_project_id',            'FOREIGN KEY (project_id)      REFERENCES folders (id)  ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('artifacts',     'fk_artifacts_role_persona_id',       'FOREIGN KEY (role_persona_id) REFERENCES personas (id) ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('artifact_deps', 'fk_artifact_deps_project_id',        'FOREIGN KEY (project_id) REFERENCES folders (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('artifact_traces','fk_artifact_traces_project_id',     'FOREIGN KEY (project_id) REFERENCES folders (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('change_events', 'fk_change_events_project_id',        'FOREIGN KEY (project_id) REFERENCES folders (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('propagation_jobs', 'fk_propagation_jobs_change_id',   'FOREIGN KEY (change_id)       REFERENCES change_events (id)  ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('propagation_jobs', 'fk_propagation_jobs_persona_id',  'FOREIGN KEY (persona_id)      REFERENCES personas (id)       ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('propagation_jobs', 'fk_propagation_jobs_conversation_id', 'FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('agent_issues',  'fk_agent_issues_project_id',         'FOREIGN KEY (project_id)          REFERENCES folders (id)    ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('agent_issues',  'fk_agent_issues_persona_id',         'FOREIGN KEY (raised_by_persona_id) REFERENCES personas (id)     ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('agent_issues',  'fk_agent_issues_change_id',          'FOREIGN KEY (change_id)           REFERENCES change_events (id) ON DELETE SET NULL');
CALL migrate_add_fk_if_missing('artifact_assumptions', 'fk_artifact_assumptions_project_id', 'FOREIGN KEY (project_id) REFERENCES folders (id) ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('artifact_requests', 'fk_artifact_requests_project_id', 'FOREIGN KEY (project_id) REFERENCES folders (id)  ON DELETE CASCADE');
CALL migrate_add_fk_if_missing('artifact_requests', 'fk_artifact_requests_persona_id', 'FOREIGN KEY (persona_id)  REFERENCES personas (id) ON DELETE SET NULL');

DROP PROCEDURE IF EXISTS migrate_add_fk_if_missing;

SET FOREIGN_KEY_CHECKS = 1;