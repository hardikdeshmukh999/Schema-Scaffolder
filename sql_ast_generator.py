from sqlalchemy import (
    MetaData, Table, Column, String, Integer, Boolean, DateTime, Date, ForeignKey
)
from sqlalchemy.schema import CreateTable
from sqlalchemy import create_mock_engine

# Attempt to import Uuid (available in SQLAlchemy 2.0)
try:
    from sqlalchemy import Uuid
except ImportError:
    # Fallback to postgresql dialect UUID for SQLAlchemy 1.4
    from sqlalchemy.dialects.postgresql import UUID as Uuid

from compiler_core import IRSchema

def _map_data_type(ir_type: str):
    """
    Map simple IR types to SQLAlchemy types.
    """
    type_map = {
        "string": String(255),
        "integer": Integer(),
        "boolean": Boolean(),
        "date": Date(),
        "uuid": Uuid(as_uuid=True),
        "timestamptz": DateTime(timezone=True)
    }
    return type_map.get(ir_type.lower(), String(255))

def compile_ir_to_sql(ir_schema: IRSchema, dialect: str = 'postgresql') -> str:
    """
    Read the Pydantic IRSchema and programmatically construct a SQLAlchemy MetaData registry.
    Compiles the MetaData into a raw SQL DDL string using a mock engine.
    """
    metadata = MetaData()
    
    # 1. Construct the MetaData registry
    for table_def in ir_schema.tables:
        columns = []
        for col_def in table_def.columns:
            kwargs = {}
            if col_def.is_primary_key:
                kwargs["primary_key"] = True
                
            if col_def.foreign_key_target:
                # foreign_key_target is expected in 'table.column' format
                columns.append(
                    Column(
                        col_def.name,
                        _map_data_type(col_def.data_type),
                        ForeignKey(col_def.foreign_key_target),
                        **kwargs
                    )
                )
            else:
                columns.append(
                    Column(
                        col_def.name,
                        _map_data_type(col_def.data_type),
                        **kwargs
                    )
                )
                
        # Instantiate the Table in the metadata registry
        Table(table_def.name, metadata, *columns)

    # 2. Compile MetaData to raw SQL DDL string
    ddl_statements = []

    def dump(sql, *multiparams, **params):
        # sql parameter here is an executable schema entity (like schema.CreateTable)
        # compile it to a string for the target dialect
        compiled_ddl = str(sql.compile(dialect=engine.dialect)).strip()
        if not compiled_ddl.endswith(';'):
            compiled_ddl += ';'
        ddl_statements.append(compiled_ddl)

    # Use SQLAlchemy's mock engine strategy to trigger the DDL generation
    engine = create_mock_engine(f"{dialect}://", executor=dump)
    metadata.create_all(engine, checkfirst=False)
    
    final_ddl = "\n\n".join(ddl_statements)
    
    # 3. Post-process to inject compliance_tags as inline SQL comments
    import re
    for table_def in ir_schema.tables:
        for col_def in table_def.columns:
            if getattr(col_def, "compliance_tags", None):
                tag_str = " /* " + " ".join(col_def.compliance_tags) + " */"
                # Match the column definition line (starts with whitespace, column name, then type)
                pattern = re.compile(rf"^(\s*{col_def.name}\s+.*?)(,?\s*)$", re.MULTILINE | re.IGNORECASE)
                final_ddl = pattern.sub(rf"\1{tag_str}\2", final_ddl)
                
    return final_ddl
