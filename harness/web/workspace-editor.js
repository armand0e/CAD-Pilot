/** Editable source/specification, independent of the chat composer. */
export class WorkspaceEditor {
  constructor({getState,onProject,toast}) {
    this.getState=getState;this.onProject=onProject;this.toast=toast;
    this.panel=document.getElementById('workspace-editor');
    this.select=document.getElementById('workspace-file');
    this.text=document.getElementById('workspace-content');
    this.status=document.getElementById('workspace-status');
    this.save=document.getElementById('workspace-save');this.build=document.getElementById('workspace-build');
    this.reload=document.getElementById('workspace-reload');this.entry=document.getElementById('workspace-entry');
    this.drafts=new Map();this.key=null;this.busy=false;
    this.panel.addEventListener('toggle',()=>{if(this.panel.open&&!this.key)this.load().catch(e=>toast(e.message,'err'));});
    this.select.onchange=()=>this.read().catch(e=>toast(e.message,'err'));
    this.text.oninput=()=>{const draft=this.drafts.get(this.key);if(draft)draft.content=this.text.value;this.sync();};
    this.reload.onclick=()=>this.load(true).catch(e=>toast(e.message,'err'));
    this.save.onclick=()=>this.saveFile().catch(e=>toast(e.message,'err'));
    this.build.onclick=()=>this.buildModel().catch(e=>toast(e.message,'err'));
  }
  async request(path,options) {const response=await fetch(path,options);const data=await response.json();if(!response.ok)throw Error(data.detail||'Workspace request failed');return data;}
  projectChanged(project) {
    if(this.projectId!==project.id){this.projectId=project.id;this.key=null;this.select.replaceChildren();this.text.value='';if(this.panel.open)this.load().catch(e=>this.toast(e.message,'err'));}
    this.sync();
  }
  sync() {
    const state=this.getState(),draft=this.drafts.get(this.key),dirty=draft&&draft.content!==draft.saved;
    const unsaved=[...this.drafts].some(([key,d])=>key.startsWith(`${state.session?.project_id}:`)&&d.content!==d.saved);
    const locked=this.busy||state.running||state.projectBusy||!state.session?.project_id||this.projectId!==state.session.project_id||state.project?.id!==state.session.project_id;
    this.text.readOnly=locked||!draft||['CAD_GUIDE.md','cad_paths.py'].includes(this.select.value);
    this.save.disabled=locked||!dirty;
    this.build.disabled=locked||!this.entry.value||unsaved;
    this.reload.disabled=this.busy;this.select.disabled=this.busy;this.entry.disabled=locked;
    this.status.textContent=this.busy?'Working…':state.running?'Stop the assistant to edit files.':dirty?'Unsaved file changes. Save before building.':draft?'File saved. Build to update the model.':'Open a file to view or edit it.';
  }
  async load(discard=false) {
    const id=this.getState().session?.project_id;if(!id)return;
    const selected=this.select.value;
    this.busy=true;this.sync();
    try {
      const data=await this.request(`/api/projects/${id}/workspace`);
      if(this.getState().session?.project_id!==id)return;
      this.select.replaceChildren();this.entry.replaceChildren();
      for(const file of data.files){
        if(/\.(py|scad|md|json|txt|svg|csv)$/i.test(file.path)&&file.bytes<=1048576){const option=document.createElement('option');option.value=file.path;option.textContent=file.path;this.select.append(option);}
        if(/\.(py|scad)$/i.test(file.path)&&file.path!=='cad_paths.py'){const option=document.createElement('option');option.value=file.path;option.textContent=file.path;this.entry.append(option);}
      }
      this.entry.value=this.getState().project?.design?.entrypoint||(this.getState().session?.app?.id?.includes('openscad')?'model.scad':'model.py');
      this.select.value=[...this.select.options].some(o=>o.value===selected)?selected:'design-spec.json';
      await this.read(discard);
    } finally {this.busy=false;this.sync();}
  }
  async read(discard=false) {
    const id=this.getState().session?.project_id,path=this.select.value;if(!id||!path)return;
    const key=`${id}:${path}`;let draft=this.drafts.get(key);
    if(!draft||discard||draft.content===draft.saved){
      const data=await this.request(`/api/projects/${id}/workspace/file?path=${encodeURIComponent(path)}`);
      if(this.getState().session?.project_id!==id||this.select.value!==path)return;
      draft={...data,saved:data.content};this.drafts.set(key,draft);
    }
    this.key=key;this.text.value=draft.content;this.sync();
  }
  async saveFile() {
    const id=this.getState().session?.project_id,key=this.key,draft=this.drafts.get(key);if(!draft||!key.startsWith(`${id}:`))return;
    this.busy=true;this.sync();
    try {
      const data=await this.request(`/api/projects/${id}/workspace/file`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:draft.path,content:draft.content,sha256:draft.sha256})});
      this.drafts.set(key,{...data,saved:data.content});
      if(this.key===key)this.text.value=data.content;
    } finally {this.busy=false;this.sync();}
  }
  async buildModel() {
    const state=this.getState(),session=state.session;if(!session)return;
    this.busy=true;this.sync();
    try {
      const project=await this.request(`/api/sessions/${session.id}/project`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:'source_build',entrypoint:this.entry.value,expected_head:state.project?.head??null})});
      if(this.getState().session?.id===session.id)this.onProject(project);
      this.toast(project.warning||`Saved ${project.head}`,project.warning?'err':undefined);
    } finally {this.busy=false;this.sync();}
  }
}
