-- Example macro for dropping CI schemas
-- Add this to your dbt project's macros/ folder
--
-- Usage in CI:
--   dbt run-operation drop_ci_schema --args '{schema_name: "ci_123_abc1234"}'
--
-- This macro supports Snowflake, BigQuery, and Databricks

{% macro drop_ci_schema(schema_name) %}

    {% if schema_name is not defined or schema_name is none %}
        {{ exceptions.raise_compiler_error("schema_name is required") }}
    {% endif %}

    {# Safety check: only drop schemas that start with 'ci_' #}
    {% if not schema_name.startswith('ci_') %}
        {{ exceptions.raise_compiler_error("Safety check failed: schema_name must start with 'ci_'") }}
    {% endif %}

    {{ log("Dropping CI schema: " ~ schema_name, info=True) }}

    {% set drop_statement %}
        {% if target.type == 'snowflake' %}
            DROP SCHEMA IF EXISTS {{ target.database }}.{{ schema_name }} CASCADE
        {% elif target.type == 'bigquery' %}
            DROP SCHEMA IF EXISTS {{ schema_name }} CASCADE
        {% elif target.type == 'databricks' %}
            DROP SCHEMA IF EXISTS {{ target.catalog }}.{{ schema_name }} CASCADE
        {% else %}
            {{ exceptions.raise_compiler_error("Unsupported target type: " ~ target.type) }}
        {% endif %}
    {% endset %}

    {% do run_query(drop_statement) %}

    {{ log("CI schema dropped successfully: " ~ schema_name, info=True) }}

{% endmacro %}


-- Macro to list and cleanup orphaned CI schemas (older than X days)
-- Usage:
--   dbt run-operation cleanup_old_ci_schemas --args '{days_old: 7}'

{% macro cleanup_old_ci_schemas(days_old=7) %}

    {{ log("Looking for CI schemas older than " ~ days_old ~ " days", info=True) }}

    {% if target.type == 'snowflake' %}
        {% set find_schemas %}
            SELECT schema_name
            FROM {{ target.database }}.information_schema.schemata
            WHERE schema_name LIKE 'ci_%'
            AND created < DATEADD(day, -{{ days_old }}, CURRENT_TIMESTAMP())
        {% endset %}
    {% elif target.type == 'bigquery' %}
        {% set find_schemas %}
            SELECT schema_name
            FROM `{{ target.project }}`.INFORMATION_SCHEMA.SCHEMATA
            WHERE schema_name LIKE 'ci_%'
            AND creation_time < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {{ days_old }} DAY)
        {% endset %}
    {% elif target.type == 'databricks' %}
        {# Databricks doesn't have easy schema age metadata, use different approach #}
        {{ log("Databricks cleanup requires manual schema tracking", info=True) }}
        {{ return([]) }}
    {% else %}
        {{ exceptions.raise_compiler_error("Unsupported target type: " ~ target.type) }}
    {% endif %}

    {% set schemas_to_drop = run_query(find_schemas) %}

    {% for row in schemas_to_drop %}
        {% set schema_name = row[0] %}
        {{ log("Dropping old CI schema: " ~ schema_name, info=True) }}
        {{ drop_ci_schema(schema_name) }}
    {% endfor %}

    {{ log("Cleanup complete. Dropped " ~ schemas_to_drop | length ~ " old CI schemas.", info=True) }}

{% endmacro %}
