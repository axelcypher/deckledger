class DeckLedgerStatPill extends HTMLElement {
  connectedCallback(){
    if(this.dataset.enhanced==='1')return;
    this.dataset.enhanced='1';
    this.classList.add('stat-pill');
    this.setAttribute('role','list');

    const normalize=(value,fallback)=>{
      const number=Number.parseInt(value,10);
      return Number.isFinite(number)&&number>0&&number<=8?number:fallback;
    };
    const columns=normalize(this.getAttribute('columns'),3);
    const mobileColumns=normalize(this.getAttribute('mobile-columns'),Math.min(columns,3));
    this.style.setProperty('--stat-pill-columns',String(columns));
    this.style.setProperty('--stat-pill-mobile-columns',String(mobileColumns));

    [...this.children].forEach(item=>{
      if(item.hasAttribute('data-stat'))item.setAttribute('role','listitem');
    });
  }
}

if(!customElements.get('deckledger-stat-pill')){
  customElements.define('deckledger-stat-pill',DeckLedgerStatPill);
}
