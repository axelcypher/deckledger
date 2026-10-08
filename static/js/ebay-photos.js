// Own photos of a card for its eBay listings (deckledger/ebay_photos.py): the strip in a draft
// and the editor that crops and turns a photo. The server cuts the listed picture from the
// original, so the editor only says where: fractions of the turned picture and the turn.

const EBAY_CROP_ASPECTS=[['free','Frei',null],['card','Karte 5:7',5/7],['square','Quadrat',1]];

function ebayPhotoStrip(draft){
  const photos=draft.photos||[];
  return `<div class="ebay-photos" data-photo-variant="${escapeHtml(draft.variant_id)}">
    ${photos.map((photo,index)=>`<button type="button" class="ebay-photo" data-photo-id="${photo.id}" title="${index?'Foto bearbeiten':'Titelbild bearbeiten'}" aria-label="Foto ${index+1} bearbeiten"><img src="${escapeHtml(photo.url)}" alt="" loading="lazy">${index?'':'<i>Titel</i>'}</button>`).join('')}
    ${photos.length<24?`<label class="ebay-photo-add" title="Fotos hinzufügen"><input type="file" accept="image/*" multiple hidden data-photo-add><span aria-hidden="true">+</span><b>Foto</b></label>`:''}
    <small class="ebay-photos-hint">${photos.length?`${photos.length} ${photos.length===1?'Foto':'Fotos'} · antippen zum Zuschneiden, Drehen oder Löschen`:'Noch kein eigenes Foto – ohne bekommt eBay nur das Kartenbild aus dem Katalog.'}</small>
  </div>`;
}

// Every draft of the card shows the same photos, so all their strips are drawn again.
async function refreshEbayPhotos(variantId,photos){
  photos??=await api(`/api/ebay/photos?variant_id=${encodeURIComponent(variantId)}`);
  $$('.ebay-photos',content).filter(strip=>strip.dataset.photoVariant===variantId).forEach(strip=>{
    strip.outerHTML=ebayPhotoStrip({variant_id:variantId,photos});
  });
  $$('.ebay-photos',content).filter(strip=>strip.dataset.photoVariant===variantId).forEach(strip=>bindEbayPhotoStrip(strip));
}

function bindEbayPhotoStrip(strip){
  const variantId=strip.dataset.photoVariant;
  $('[data-photo-add]',strip)?.addEventListener('change',async event=>{
    const files=[...event.target.files];event.target.value='';
    for(const [index,file] of files.entries()){
      const src=URL.createObjectURL(file);
      const saved=await openEbayCropper({src,title:files.length>1?`Foto ${index+1} von ${files.length} zuschneiden`:'Foto zuschneiden',
        save:async crop=>{
          const form=new FormData();form.append('variant_id',variantId);form.append('file',file);form.append('crop',JSON.stringify(crop));
          const response=await fetch('/api/ebay/photos',{method:'POST',body:form}),created=await response.json().catch(()=>({}));
          if(!response.ok)throw new Error(created.error||`Upload fehlgeschlagen (${response.status})`);
        }});
      URL.revokeObjectURL(src);
      if(saved===null)break;   // cancelled: the files after it are not asked for either
    }
    refreshEbayPhotos(variantId).catch(error=>toast(error.message));
  });
  $$('[data-photo-id]',strip).forEach(button=>button.onclick=async()=>{
    const photos=await api(`/api/ebay/photos?variant_id=${encodeURIComponent(variantId)}`).catch(error=>{toast(error.message);return null});
    const photo=photos?.find(item=>item.id===Number(button.dataset.photoId));if(!photo)return;
    const first=photos[0].id===photo.id;
    await openEbayCropper({src:photo.original_url,crop:photo.crop,title:'Foto bearbeiten',
      save:async crop=>{await api(`/api/ebay/photos/${photo.id}`,{method:'PATCH',body:JSON.stringify({crop})})},
      extra:[
        ...(first?[]:[['Als Titelbild',async()=>{await post('/api/ebay/photos/order',{variant_id:variantId,ids:[photo.id,...photos.filter(item=>item.id!==photo.id).map(item=>item.id)]})}]]),
        ['Löschen',async()=>{if(!confirm('Dieses Foto löschen?'))return false;await api(`/api/ebay/photos/${photo.id}`,{method:'DELETE'})},'danger'],
      ]});
    refreshEbayPhotos(variantId).catch(error=>toast(error.message));
  });
}

// The editor. Resolves with true once save() went through, false after an extra action, null
// when cancelled. save(crop) and the extra actions throw to keep the editor open with a message.
function openEbayCropper({src,crop,title,save,extra=[]}){
  return new Promise(resolve=>{
    const modal=openSettingsModal({id:'ebay-crop-modal',eyebrow:'EBAY · FOTO',title,
      body:`<div class="ebay-crop-stage" id="ebay-crop-stage"><canvas></canvas><div class="ebay-crop-box" hidden>${['nw','ne','sw','se'].map(corner=>`<span class="ebay-crop-handle is-${corner}" data-corner="${corner}"></span>`).join('')}</div><div class="page-loader compact"><span></span></div></div>
        <div class="ebay-crop-tools">
          <div class="deal-filter-chips">${EBAY_CROP_ASPECTS.map(([id,label])=>`<button type="button" class="community-chip" data-crop-aspect="${id}">${label}</button>`).join('')}</div>
          <span class="spacer"></span>
          <button type="button" class="icon-button" data-crop-rotate="-90" title="Nach links drehen" aria-label="Nach links drehen">↺</button>
          <button type="button" class="icon-button" data-crop-rotate="90" title="Nach rechts drehen" aria-label="Nach rechts drehen">↻</button>
          <button type="button" class="secondary-button" data-crop-reset>Ganzes Bild</button>
        </div>
        <p class="dl-hint ebay-crop-message" hidden></p>
        <div class="dl-modal-actions">${extra.map(([label,,kind],index)=>`<button type="button" class="${kind==='danger'?'danger-button':'secondary-button'}" data-crop-extra="${index}">${label}</button>`).join('')}<span class="spacer"></span>
          <button type="button" class="secondary-button" data-crop-cancel>Abbrechen</button><button type="button" class="primary-button" data-crop-save disabled>Übernehmen</button></div>`});
    modal.classList.add('ebay-crop-overlay');
    const stage=$('#ebay-crop-stage',modal),canvas=$('canvas',stage),box=$('.ebay-crop-box',stage),message=$('.ebay-crop-message',modal);
    const image=new Image();
    let rotate=crop?.rotate||0,aspect='free',rect=crop?{x:crop.x,y:crop.y,w:crop.w,h:crop.h}:{x:0,y:0,w:1,h:1},done=false;
    const finish=value=>{if(done)return;done=true;observer.disconnect();closeSettingsModal();resolve(value)};
    // Closing the dialog any other way (×, Escape, a click beside it) counts as cancelling.
    const observer=new MutationObserver(()=>{if(!modal.isConnected&&!done){done=true;observer.disconnect();resolve(null)}});
    observer.observe(document.body,{childList:true});
    const say=text=>{message.hidden=!text;message.textContent=text||''};

    // The turned picture drawn as large as the stage allows; the box is positioned in its pixels.
    const draw=()=>{
      if(!image.naturalWidth)return;
      const turned=rotate%180!==0,width=turned?image.naturalHeight:image.naturalWidth,height=turned?image.naturalWidth:image.naturalHeight;
      const maxWidth=stage.clientWidth||600,maxHeight=Math.min(window.innerHeight*.58,640);
      const scale=Math.min(maxWidth/width,maxHeight/height,1);
      canvas.width=Math.round(width*scale);canvas.height=Math.round(height*scale);
      const context=canvas.getContext('2d');
      context.save();context.translate(canvas.width/2,canvas.height/2);context.rotate(rotate*Math.PI/180);
      const drawWidth=image.naturalWidth*scale,drawHeight=image.naturalHeight*scale;
      context.drawImage(image,-drawWidth/2,-drawHeight/2,drawWidth,drawHeight);context.restore();
      place();
    };
    const place=()=>{
      box.hidden=false;
      Object.assign(box.style,{left:`${canvas.offsetLeft+rect.x*canvas.width}px`,top:`${canvas.offsetTop+rect.y*canvas.height}px`,width:`${rect.w*canvas.width}px`,height:`${rect.h*canvas.height}px`});
    };
    // The largest box of the chosen shape, centred, when the shape or the turn changes.
    const fit=()=>{
      const ratio=EBAY_CROP_ASPECTS.find(([id])=>id===aspect)[2];
      if(!ratio){rect={x:0,y:0,w:1,h:1};return}
      let w=canvas.width,h=w/ratio;
      if(h>canvas.height){h=canvas.height;w=h*ratio}
      rect={x:(1-w/canvas.width)/2,y:(1-h/canvas.height)/2,w:w/canvas.width,h:h/canvas.height};
    };
    const markAspect=()=>$$('[data-crop-aspect]',modal).forEach(button=>button.classList.toggle('active',button.dataset.cropAspect===aspect));

    image.onload=()=>{$('.page-loader',stage)?.remove();draw();$('[data-crop-save]',modal).disabled=false};
    image.onerror=()=>{$('.page-loader',stage)?.remove();say('Das Bild lässt sich nicht öffnen. Ist es ein JPEG, PNG oder WebP?')};
    image.src=src;
    markAspect();
    const onResize=()=>{if(modal.isConnected)draw();else window.removeEventListener('resize',onResize)};
    window.addEventListener('resize',onResize);

    // Dragging: inside the box moves it, a corner resizes it (keeping the shape if one is chosen).
    let drag=null;
    box.addEventListener('pointerdown',event=>{
      event.preventDefault();box.setPointerCapture(event.pointerId);
      drag={corner:event.target.dataset.corner||null,startX:event.clientX,startY:event.clientY,start:{...rect}};
    });
    box.addEventListener('pointermove',event=>{
      if(!drag)return;
      const dx=(event.clientX-drag.startX)/canvas.width,dy=(event.clientY-drag.startY)/canvas.height,s=drag.start;
      const min=40/Math.max(canvas.width,canvas.height);
      if(!drag.corner){
        rect={...s,x:Math.min(1-s.w,Math.max(0,s.x+dx)),y:Math.min(1-s.h,Math.max(0,s.y+dy))};
      }else{
        const west=drag.corner.includes('w'),north=drag.corner.includes('n');
        // The corner opposite the dragged one stays where it is.
        const fixedX=west?s.x+s.w:s.x,fixedY=north?s.y+s.h:s.y;
        let w=Math.max(min,west?s.w-dx:s.w+dx),h=Math.max(min,north?s.h-dy:s.h+dy);
        w=Math.min(w,west?fixedX:1-fixedX);h=Math.min(h,north?fixedY:1-fixedY);
        const ratio=EBAY_CROP_ASPECTS.find(([id])=>id===aspect)[2];
        if(ratio){
          // In pixels the box is ratio wide per unit high; follow whichever side moved more, then fit.
          const px=w*canvas.width,py=h*canvas.height;
          if(px/py>ratio)w=py*ratio/canvas.width;else h=px/ratio/canvas.height;
        }
        rect={x:west?fixedX-w:fixedX,y:north?fixedY-h:fixedY,w,h};
      }
      place();
    });
    const release=()=>{drag=null};
    box.addEventListener('pointerup',release);box.addEventListener('pointercancel',release);

    $$('[data-crop-aspect]',modal).forEach(button=>button.onclick=()=>{aspect=button.dataset.cropAspect;markAspect();fit();place()});
    $$('[data-crop-rotate]',modal).forEach(button=>button.onclick=()=>{rotate=(rotate+Number(button.dataset.cropRotate)+360)%360;draw();fit();place()});
    $('[data-crop-reset]',modal).onclick=()=>{aspect='free';markAspect();rect={x:0,y:0,w:1,h:1};place()};
    $('[data-crop-cancel]',modal).onclick=()=>finish(null);
    const busy=async(button,task,result)=>{
      const label=button.textContent;button.disabled=true;button.textContent='Wird gespeichert …';say('');
      try{if(await task()===false){button.disabled=false;button.textContent=label;return}finish(result)}
      catch(error){say(error.message);button.disabled=false;button.textContent=label}
    };
    $('[data-crop-save]',modal).onclick=event=>busy(event.currentTarget,()=>save({...rect,rotate}),true);
    $$('[data-crop-extra]',modal).forEach(button=>button.onclick=()=>busy(button,extra[Number(button.dataset.cropExtra)][1],false));
  });
}
