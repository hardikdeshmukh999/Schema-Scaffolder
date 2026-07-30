import streamlit as st
import json
import os
from dotenv import load_dotenv

load_dotenv()
# Force Streamlit reload for compiler_core
from compiler_core import generate_canonical_ir, inject_enterprise_governance, BRDInput, sort_stories_by_dependency, tag_pii_hipaa_metadata

def generate_mermaid_dag(stories) -> str:
    import re
    lines = ["graph LR;"]
    
    story_map = {story.id: story for story in stories}
    edges = set()
    
    for story in stories:
        node_id = story.id.replace("-", "")
        title = story.storyTitle.replace('"', "'")
        lines.append(f'    {node_id}["{story.id}: {title}"]')
        
        for dep_str in (story.dependencies or []):
            extracted_ids = re.findall(r'\b(S-\d{3}|EXT-[A-Z]+)\b', dep_str)
            is_blocking = "Blocks " in dep_str
            
            for e_id in extracted_ids:
                if e_id in story_map:
                    e_node_id = e_id.replace("-", "")
                    if is_blocking:
                        edges.add(f'    {node_id} --> {e_node_id}')
                    else:
                        edges.add(f'    {e_node_id} --> {node_id}')
                        
    lines.extend(sorted(list(edges)))
    return "\n".join(lines)
from sql_ast_generator import compile_ir_to_sql
from linter import run_static_analysis

st.set_page_config(page_title="Enterprise Schema Scaffolder", layout="wide")

st.title("Enterprise Schema Scaffolder 🚀")
st.markdown("Pipeline: BRD Ingestion -> Canonical IR -> Governance Injection -> SQL AST Generation -> Component Registry")

# Initialize session state variables if they don't exist
if 'compliant_ir' not in st.session_state:
    st.session_state.compliant_ir = None
if 'sql_ddl' not in st.session_state:
    st.session_state.sql_ddl = None
if 'linter_results' not in st.session_state:
    st.session_state.linter_results = None
if 'error' not in st.session_state:
    st.session_state.error = None
if 'sorted_stories' not in st.session_state:
    st.session_state.sorted_stories = None

input_dir = "input_data"
if os.path.exists(input_dir):
    json_files = [f for f in os.listdir(input_dir) if f.endswith('.json')]
else:
    json_files = []

if not json_files:
    json_files = ["brd_input.json"] # fallback if no folder

selected_file = st.selectbox("Select Input BRD JSON", json_files)

if st.button("Run Scaffolder Pipeline", type="primary"):
    st.session_state.error = None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.session_state.error = "Please set the ANTHROPIC_API_KEY environment variable in the .env file."
    else:
        try:
            file_path = os.path.join(input_dir, selected_file)
            with open(file_path, "r", encoding="utf-8") as f:
                brd_json = json.load(f)
                
            with st.spinner("Phase 1: Validating and Topologically Sorting BRD..."):
                st.session_state.sorted_stories = sort_stories_by_dependency(BRDInput.model_validate(brd_json))
                
            with st.spinner("Phase 2: Generating Canonical IR (Anthropic API)..."):
                raw_ir = generate_canonical_ir(brd_json)
                
            with st.spinner("Phase 3: Injecting Enterprise Governance Columns..."):
                gov_ir = inject_enterprise_governance(raw_ir)
                
            with st.spinner("Phase 3.5: Semantic Tagging (PII/HIPAA)..."):
                st.session_state.compliant_ir = tag_pii_hipaa_metadata(gov_ir)
                
            with st.spinner("Phase 5: Compiling SQL AST (DDL)..."):
                st.session_state.sql_ddl = compile_ir_to_sql(st.session_state.compliant_ir, dialect="postgresql")
                
            with st.spinner("Phase 5: Running Static Analysis Linter..."):
                st.session_state.linter_results = run_static_analysis(st.session_state.compliant_ir, st.session_state.sql_ddl)

        except Exception as e:
            st.session_state.error = f"Pipeline execution failed: {str(e)}"

# Error handling
if st.session_state.get("error"):
    st.error(st.session_state.get("error"))
elif st.session_state.get("compliant_ir"):
    st.success("Pipeline executed successfully!")

# Always render the tabs!
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Phase 1: DAG", 
    "Phase 2-3: IR & Governance", 
    "Phase 3.5: Semantic Tagging", 
    "Phase 5: SQL AST", 
    "Phase 5: Static Analysis"
])

with tab1:
    st.header("Phase 1: BRD Validation & DAG")
    st.markdown("Topologically sorts user stories based on dependencies to ensure correct entity processing order.")
    if st.session_state.get("sorted_stories"):
        st.success("✅ BRD Validated & Topologically Sorted")
        mermaid_code = generate_mermaid_dag(st.session_state.get("sorted_stories"))
        st.markdown(f"```mermaid\n{mermaid_code}\n```")
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab2:
    st.header("Phase 2-3: Canonical IR & Governance")
    st.markdown("Multi-agent extraction of domain entities, deduplication, and deterministic injection of audit columns (`id`, `created_at`, etc.).")
    if st.session_state.get("compliant_ir"):
        st.success("✅ Canonical IR Generated & Governed")
        st.json(st.session_state.get("compliant_ir").model_dump())
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab3:
    st.header("Phase 3.5: Semantic Tagging (PII/HIPAA)")
    st.markdown("AI compliance officer identifies sensitive columns and applies `@pii_masked` or `@hipaa_classified` metadata.")
    if st.session_state.get("compliant_ir"):
        st.success("✅ Semantic Tagging Applied")
        tagged_cols = []
        for t in st.session_state.get("compliant_ir").tables:
            for c in t.columns:
                if c.compliance_tags:
                    tagged_cols.append({"Table": t.name, "Column": c.name, "Tags": ", ".join(c.compliance_tags)})
        if tagged_cols:
            st.table(tagged_cols)
        else:
            st.info("No sensitive columns detected.")
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab4:
    st.header("Phase 5: Compiled SQL AST (DDL)")
    st.markdown("Compiles the Intermediate Representation into deployment-ready PostgreSQL DDL with inline compliance comments.")
    if st.session_state.get("sql_ddl"):
        st.code(st.session_state.get("sql_ddl"), language="sql")
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab5:
    st.header("Phase 5: Static Analysis Linter")
    st.markdown("Verifies PostgreSQL syntax, relational integrity, and foreign key validations.")
    linter_res = st.session_state.get("linter_results")
    if linter_res is not None:
        if linter_res["status"] == "Pass":
            st.success("Linter Passed")
        else:
            st.error("Linter Failed")
        log_text = "\n".join(linter_res["logs"])
        st.code(log_text, language="text")
    else:
        st.info("Run the pipeline to generate this data.")
