import os
import json
import concurrent.futures
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from anthropic import Anthropic
from graphlib import TopologicalSorter

# -------------------------------------------------------------------
# Input BRD Schema (Epics, Stories, Acceptance Criteria)
# -------------------------------------------------------------------

class AcceptanceCriteria(BaseModel):
    id: str
    criteria: str

class Story(BaseModel):
    id: str
    storyTitle: str
    epicName: str
    userStory: str
    description: str
    complexity: str
    storyPoints: int
    priority: str
    acceptanceCriteria: List[AcceptanceCriteria]
    dependencies: Optional[List[str]] = Field(default_factory=list)

class Epic(BaseModel):
    epicName: str
    description: str
    businessValue: str
    brdReferences: List[str]

class BRDInput(BaseModel):
    epics: List[Epic]
    stories: List[Story]

# -------------------------------------------------------------------
# Canonical IR Schema (Database Schema)
# -------------------------------------------------------------------

class ColumnDef(BaseModel):
    name: str
    data_type: Literal["string", "integer", "boolean", "date", "uuid", "timestamptz"]
    is_primary_key: bool = False
    foreign_key_target: Optional[str] = None  # Format: 'table.column'
    compliance_tags: List[str] = Field(default_factory=list)

class TableDef(BaseModel):
    name: str
    columns: List[ColumnDef]

class IRSchema(BaseModel):
    tables: List[TableDef]

# -------------------------------------------------------------------
# Phase 1 & 2: Ingestion and Canonical IR Generation
# -------------------------------------------------------------------

def sort_stories_by_dependency(brd_data: BRDInput) -> List[Story]:
    """
    Phase 1: Dependency Graphing.
    Uses TopologicalSorter to guarantee foundational entities are processed first.
    """
    ts = TopologicalSorter()
    story_map = {story.id: story for story in brd_data.stories}
    
    # Check if there are explicit dependencies
    has_explicit_deps = any(story.dependencies for story in brd_data.stories)
    
    if has_explicit_deps:
        # Pre-populate all nodes to prevent missing orphans
        for story in brd_data.stories:
            ts.add(story.id)
            
        import re
        for story in brd_data.stories:
            for dep_str in (story.dependencies or []):
                # Extract IDs like S-007
                extracted_ids = re.findall(r'\b(S-\d{3}|EXT-[A-Z]+)\b', dep_str)
                is_blocking = "Blocks " in dep_str
                
                for e_id in extracted_ids:
                    if e_id in story_map:
                        if is_blocking:
                            # e_id depends on story.id
                            ts.add(e_id, story.id)
                        else:
                            # story.id depends on e_id
                            ts.add(story.id, e_id)
    else:
        # Fallback to Epic hierarchy order
        foundation_keywords = ["auth", "access", "foundation", "platform"]
        
        foundation_ids = [s.id for s in brd_data.stories if any(k in s.epicName.lower() for k in foundation_keywords)]
        dependent_ids = [s.id for s in brd_data.stories if s.id not in foundation_ids]
        
        for f_id in foundation_ids:
            ts.add(f_id)
        for d_id in dependent_ids:
            ts.add(d_id, *foundation_ids)
            
    sorted_ids = list(ts.static_order())
    return [story_map[sid] for sid in sorted_ids if sid in story_map]

def _call_anthropic(client: Anthropic, system_prompt: str, user_prompt: str) -> str:
    models = ["claude-sonnet-5", "claude-sonnet-4-6", "claude-sonnet-4-5-20250929"]
    last_error = None
    
    for model in models:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
                extra_headers={"anthropic-beta": "max-tokens-3-5-sonnet-2024-07-15"}
            )
            
            response_text = ""
            for block in response.content:
                if getattr(block, 'type', None) == 'text' or hasattr(block, 'text'):
                    response_text = block.text.strip()
                    break
            
            if response_text.startswith("```"):
                lines = response_text.split('\n')
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                response_text = "\n".join(lines).strip()
                
            return response_text
        except Exception as e:
            last_error = e
            if "not_found_error" in str(e):
                continue
            raise e
    raise last_error

def extract_entities(story: Story, client: Anthropic) -> List[dict]:
    system_prompt = "You are an Entity Extractor. Read this Agile user story and return ONLY a JSON list of the core domain entities (nouns) required to build this feature. Do not define columns."
    user_prompt = f"Extract entities from this story:\n\n{story.model_dump_json(indent=2)}\n\nReturn ONLY a JSON list."
    response = _call_anthropic(client, system_prompt, user_prompt)
    try:
        return json.loads(response)
    except:
        return []

def deduplicate_entities(entity_lists: List[List[dict]], client: Anthropic) -> List[dict]:
    system_prompt = "You are a Data Architect. I will provide lists of entities extracted from various stories. Your job is to merge overlapping entities (e.g., 'Manager' and 'New Hire' should become a single 'User' entity with a role attribute). Return a unified JSON list of unique canonical entities."
    user_prompt = f"Deduplicate these entity lists:\n\n{json.dumps(entity_lists, indent=2)}\n\nReturn ONLY a JSON list of unique entities."
    response = _call_anthropic(client, system_prompt, user_prompt)
    try:
        return json.loads(response)
    except:
        return []

def model_schema(unique_entities: List[dict], brd_json: dict, client: Anthropic) -> IRSchema:
    system_prompt = "You are a Database Modeler. Take this list of unique entities and the original BRD context, and map them into the strict IRSchema Pydantic model. Define the TableDef and ColumnDef structures, including data types and foreign keys. Return ONLY valid JSON matching the provided schema."
    user_prompt = f"BRD Context:\n{json.dumps(brd_json, indent=2)}\n\nUnique Entities:\n{json.dumps(unique_entities, indent=2)}\n\nJSON Schema constraint:\n{json.dumps(IRSchema.model_json_schema(), indent=2)}\n\nReturn ONLY JSON matching the schema."
    response = _call_anthropic(client, system_prompt, user_prompt)
    ir_data = json.loads(response)
    return IRSchema.model_validate(ir_data)

def generate_canonical_ir(brd_json: dict) -> IRSchema:
    """
    Ingests raw BRD JSON, validates it against the BRDInput schema,
    and uses an Orchestrator-Worker Multi-Agent architecture to generate the IR.
    """
    validated_brd = BRDInput.model_validate(brd_json)
    validated_brd.stories = sort_stories_by_dependency(validated_brd)
    
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    # Orchestrator Phase 1: Parallel Entity Extraction
    entity_lists = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_story = {executor.submit(extract_entities, story, client): story for story in validated_brd.stories}
        for future in concurrent.futures.as_completed(future_to_story):
            try:
                entities = future.result()
                if entities:
                    entity_lists.append(entities)
            except Exception as exc:
                print(f"Extractor agent failed for a story: {exc}")
                
    # Orchestrator Phase 2: Deduplication
    unique_entities = deduplicate_entities(entity_lists, client)
    
    # Orchestrator Phase 3: Schema Modeling
    canonical_ir = model_schema(unique_entities, brd_json, client)
    
    return canonical_ir

# -------------------------------------------------------------------
# Phase 3: Compliance Injection
# -------------------------------------------------------------------

def inject_enterprise_governance(ir_schema: IRSchema) -> IRSchema:
    """
    Programmatically injects standard enterprise governance columns into every table.
    Ensures standard 'id', 'created_at', 'updated_at', 'created_by', and 'is_deleted' columns.
    """
    for table in ir_schema.tables:
        existing_cols = {col.name: col for col in table.columns}
        
        # 1. Refactor existing primary keys to not be primary keys, if they are not 'id'
        for col in table.columns:
            if col.is_primary_key and col.name != "id":
                col.is_primary_key = False
                
        # 2. Inject or update 'id'
        if "id" not in existing_cols:
            table.columns.insert(0, ColumnDef(
                name="id",
                data_type="uuid",
                is_primary_key=True
            ))
        else:
            existing_cols["id"].data_type = "uuid"
            existing_cols["id"].is_primary_key = True
            
        # 3. Inject 'created_at'
        if "created_at" not in existing_cols:
            table.columns.append(ColumnDef(
                name="created_at",
                data_type="timestamptz"
            ))
            
        # 4. Inject 'updated_at'
        if "updated_at" not in existing_cols:
            table.columns.append(ColumnDef(
                name="updated_at",
                data_type="timestamptz"
            ))
            
        # 5. Inject 'created_by'
        if "created_by" not in existing_cols:
            table.columns.append(ColumnDef(
                name="created_by",
                data_type="uuid"
            ))
            
        # 6. Inject 'is_deleted'
        if "is_deleted" not in existing_cols:
            table.columns.append(ColumnDef(
                name="is_deleted",
                data_type="boolean"
            ))
            
    return ir_schema

def tag_pii_hipaa_metadata(ir_schema: IRSchema) -> IRSchema:
    """
    Phase 3: Semantic Tagging.
    Uses Anthropic SDK to classify sensitive PII/PHI fields and apply masking rules.
    """
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    system_prompt = "You are a Healthcare Compliance Security Officer. Review this database schema. Identify any columns that would contain Personally Identifiable Information (PII) or Protected Health Information (PHI) based on HIPAA standards (e.g., identity documents, tax records, birth dates, medical data). Return the exact same JSON schema, but populate the compliance_tags array for sensitive columns with '@pii_masked' or '@hipaa_classified'. Do not alter any other schema structures."
    user_prompt = f"Schema:\n\n{ir_schema.model_dump_json(indent=2)}\n\nReturn ONLY a JSON matching the provided schema exactly, with compliance_tags populated for sensitive columns."
    
    response = _call_anthropic(client, system_prompt, user_prompt)
    try:
        ir_data = json.loads(response)
        return IRSchema.model_validate(ir_data)
    except Exception as e:
        print(f"Error in tag_pii_hipaa_metadata: {e}")
        return ir_schema
