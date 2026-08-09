class DeckLedgerFilterPanel extends HTMLElement {
  connectedCallback(){
    if(this.dataset.enhanced==='1')return;
    this.dataset.enhanced='1';
    this.classList.add('mobile-filter-shell');
    const children=[...this.childNodes];
    const label=this.getAttribute('label')||'Filter';
    const open=this.hasAttribute('open');
    this.replaceChildren();

    const toggle=document.createElement('button');
    toggle.type='button';
    toggle.className='mobile-filter-toggle';
    toggle.dataset.mobileFilterToggle='';
    toggle.setAttribute('aria-expanded',String(open));
    toggle.innerHTML=`<span class="mobile-filter-toggle-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 6h16M7 12h10M10 18h4"/><circle cx="8" cy="6" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="12" cy="18" r="1.5"/></svg></span><span>${this.escape(label)}</span><i aria-hidden="true">⌄</i>`;

    const body=document.createElement('div');
    body.className='mobile-filter-body';
    const surface=document.createElement('div');
    surface.className='mobile-filter-surface';
    surface.append(...children);
    body.append(surface);
    this.append(toggle,body);
    this.classList.toggle('is-open',open);

    toggle.addEventListener('click',()=>{
      const expanded=!this.classList.contains('is-open');
      this.classList.toggle('is-open',expanded);
      this.toggleAttribute('open',expanded);
      toggle.setAttribute('aria-expanded',String(expanded));
      this.dispatchEvent(new CustomEvent('deckledger-filter-toggle',{
        bubbles:true,
        detail:{key:this.getAttribute('filter-key')||'',open:expanded},
      }));
    });
  }

  get contentElement(){ return this.querySelector('.mobile-filter-surface'); }

  escape(value){
    const span=document.createElement('span');
    span.textContent=String(value);
    return span.innerHTML;
  }
}

if(!customElements.get('deckledger-filter-panel')){
  customElements.define('deckledger-filter-panel',DeckLedgerFilterPanel);
}
