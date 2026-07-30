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
if st.session_state.error:
    st.error(st.session_state.error)
elif st.session_state.compliant_ir:
    st.success("Pipeline executed successfully!")

# Always render the tabs!
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Tab 1: 📥 Ingestion & DAG", 
    "Tab 2: SQL DDL", 
    "Tab 3 (Skipped)", 
    "Tab 4 (Skipped)", 
    "Tab 5: 🛡️ Static Analysis"
])

with tab1:
    st.header("JSON Intermediate Representation & DAG")
    st.markdown("Phase 1 Output (Topological Sort) and Phase 2 & 3 (Canonical IR)")
    if st.session_state.sorted_stories and st.session_state.compliant_ir:
        st.success("✅ BRD Validated & Topologically Sorted")
        mermaid_code = generate_mermaid_dag(st.session_state.sorted_stories)
        st.markdown(f"```mermaid\n{mermaid_code}\n```")
        st.json(st.session_state.compliant_ir.model_dump())
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab2:
    st.header("Compiled SQL DDL")
    st.markdown("Phase 5 Output (PostgreSQL AST compiled via SQLAlchemy)")
    if st.session_state.sql_ddl:
        st.code(st.session_state.sql_ddl, language="sql")
    else:
        st.info("Run the pipeline to generate this data.")
        
with tab3:
    st.write("Tab 3 skipped for this sprint to ensure core stability.")
    
with tab4:
    st.write("Tab 4 skipped for this sprint to ensure core stability.")
    
with tab5:
    st.header("Static Analysis Linter")
    st.markdown("Phase 5 Output (SQL Syntax and Integrity Checks)")
    if getattr(st.session_state, "linter_results", None) is not None:
        if st.session_state.linter_results["status"] == "Pass":
            st.success("Linter Passed")
        else:
            st.error("Linter Failed")
        log_text = "\n".join(st.session_state.linter_results["logs"])
        st.code(log_text, language="text")
    else:
        st.info("Run the pipeline to generate this data.")
