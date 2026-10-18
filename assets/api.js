/* Same-origin backend; writes are never automatically retried. */
(function(){
  // Loading feedback: a request started from a button click marks that button busy
  // (spinner + disabled, which also prevents double submits) and shows a top progress bar.
  // Background refreshes (polling) are not started by a click, so they stay silent.
  var hasDom=typeof document!=='undefined'&&!!document.addEventListener;
  var clicked=null,activeCount=0,bar=null;
  // Requests count as user activity (keeping the login alive) only when the person
  // interacted with the page recently, or for non-background actions. Auto-refresh does not.
  var lastInteraction=Date.now();
  var BACKGROUND={dataPanitia:1,dataKoordinator:1,logBroadcast:1,tvData:1,prosesAntrean:1,statusKonfirmasi:1,pengaturan:1,getWaFormat:1};
  function isActive(name){return !BACKGROUND[name]||Date.now()-lastInteraction<60000;}
  if(hasDom){
    ['pointerdown','keydown','touchstart'].forEach(function(t){document.addEventListener(t,function(){lastInteraction=Date.now();},true);});
    document.addEventListener('click',function(e){
      var t=e.target,b=t&&t.closest?t.closest('button,.button,[role="button"]'):null;
      if(!b)return; clicked=b; setTimeout(function(){if(clicked===b)clicked=null;},0);
    },true);
    var css='.is-loading{pointer-events:none}'+
      '.is-loading::before{content:"";display:inline-block;width:14px;height:14px;margin-right:2px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:rpc-spin .7s linear infinite;vertical-align:-2px;flex:none}'+
      '@keyframes rpc-spin{to{transform:rotate(360deg)}}'+
      '#rpcBar{position:fixed;top:0;left:0;right:0;height:3px;z-index:9999;overflow:hidden;opacity:0;transition:opacity .2s;pointer-events:none}'+
      '#rpcBar.on{opacity:1}#rpcBar::after{content:"";position:absolute;top:0;bottom:0;width:40%;background:#c7a85b;animation:rpc-bar 1s ease-in-out infinite}'+
      '@keyframes rpc-bar{from{left:-40%}to{left:100%}}'+
      '#rpcToast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);max-width:min(560px,calc(100% - 32px));padding:12px 16px;border-radius:6px;background:#1d2b20;border:1px solid #3d5943;color:#f5f5f2;font-size:13px;line-height:1.5;z-index:9999;box-shadow:0 8px 24px rgba(0,0,0,.4)}'+
      '#rpcToast.err{background:#3a1f1c;border-color:#c0392b}'+
      '@media (prefers-reduced-motion:reduce){.is-loading::before,#rpcBar::after{animation-duration:2.5s}}';
    var style=document.createElement('style');style.textContent=css;(document.head||document.documentElement).appendChild(style);
  }
  function setBar(on){
    if(!hasDom)return;
    if(!bar){bar=document.createElement('div');bar.id='rpcBar';bar.setAttribute('aria-hidden','true');document.body.appendChild(bar);}
    bar.className=on?'on':'';
  }
  function beginLoading(){
    var b=clicked;clicked=null;
    if(!b||b.classList.contains('is-loading'))return null;
    b.classList.add('is-loading');b.setAttribute('aria-busy','true');
    b.dataset.rpcWasDisabled=b.disabled?'1':'';if('disabled' in b)b.disabled=true;
    activeCount++;setBar(true);
    return b;
  }
  function endLoading(b){
    if(!b)return;
    b.classList.remove('is-loading');b.removeAttribute('aria-busy');
    if('disabled' in b)b.disabled=b.dataset.rpcWasDisabled==='1';
    activeCount=Math.max(0,activeCount-1);if(!activeCount)setBar(false);
  }
  function run(handlers){
    handlers=handlers||{};
    return new Proxy({}, {get:function(_,name){
      if(name==='withSuccessHandler') return function(fn){return run({ok:fn,err:handlers.err});};
      if(name==='withFailureHandler') return function(fn){return run({ok:handlers.ok,err:fn});};
      if(name==='withUserObject') return function(){return run(handlers);};
      return function(){
        var args=Array.prototype.slice.call(arguments);
        var busy=beginLoading();
        var controller=new AbortController();
        var timer=setTimeout(function(){controller.abort();},65000);
        fetch('/api/rpc',{method:'POST',credentials:'same-origin',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({fn:String(name),args:args,aktif:isActive(String(name))})})
          .then(async function(response){
            var data;
            try{data=await response.json();}catch(e){throw new Error('Backend mandiri tidak merespons. Jalankan server.py dan buka port 4180.');}
            if(!response.ok){var failure=new Error(data.error||'Permintaan gagal.');failure.status=response.status;throw failure;}
            return data;
          }).then(function(data){clearTimeout(timer);endLoading(busy);if(handlers.ok)handlers.ok(data);},function(error){
            clearTimeout(timer);endLoading(busy);
            // Login expired (6 hours without activity): show the PIN form again.
            if(error.status===403&&/Silakan masuk/.test(error.message)&&hasDom&&document.getElementById('pinGate')&&document.getElementById('app')&&document.getElementById('app').style.display==='block'&&!window.__rpcReloading){
              window.__rpcReloading=true;try{location.reload();}catch(x){}return;
            }
            var e={message:error.name==='AbortError'?'Respons server terlambat. Periksa data sebelum mengulangi operasi.':error.message};
            if(handlers.err)handlers.err(e);else alert(e.message);
          }).catch(function(error){console.error('Gagal menampilkan hasil',error);});
      };
    }});
  }
  window.google=window.google||{};
  window.google.script={run:run()};

  // Remembered login: if this browser already signed in for `role` (and has been active
  // within the last 6 hours), skip the PIN form. onOk opens the dashboard.
  window.cekSesi=function(role,onOk){
    if(!hasDom)return;
    var gate=document.getElementById('pinGate');
    if(gate)gate.style.visibility='hidden';
    function show(){if(gate)gate.style.visibility='';}
    window.google.script.run.withSuccessHandler(function(j){
      if(j&&j.roles&&j.roles.indexOf(role)>=0){onOk();window.pasangKeluar();}
      show();
    }).withFailureHandler(show).sesi();
  };
  // Adds a "Keluar" (log out) link to the page navigation once signed in.
  window.pasangKeluar=function(){
    if(!hasDom||document.getElementById('btnKeluar'))return;
    var nav=document.querySelector('header .nav');if(!nav)return;
    var a=document.createElement('a');a.id='btnKeluar';a.href='#';a.textContent='Keluar';
    a.addEventListener('click',function(ev){ev.preventDefault();
      if(!confirm('Keluar dari semua halaman pengelola di perangkat ini?'))return;
      window.google.script.run.withSuccessHandler(function(){location.reload();}).withFailureHandler(function(){location.reload();}).keluar();
    });
    var last=nav.lastElementChild;nav.insertBefore(a,last&&last.getAttribute('href')==='index.html'?last:null);
  };

  // Short on-screen notice (used instead of blocking alert boxes).
  var toastTimer=null;
  window.rpcToast=function(message,isError){
    if(!hasDom){return;}
    var t=document.getElementById('rpcToast');
    if(!t){t=document.createElement('div');t.id='rpcToast';t.setAttribute('role','status');t.setAttribute('aria-live','polite');document.body.appendChild(t);}
    t.textContent=message;t.className=isError?'err':'';t.style.display='block';
    clearTimeout(toastTimer);toastTimer=setTimeout(function(){t.style.display='none';},isError?9000:6000);
  };

  // Send a message from the user's own WhatsApp (app or WhatsApp Web), no gateway needed.
  // prepare: backend function returning {ready, wa_url, nama}; mark: backend function that records the manual send.
  window.kirimViaWa=function(prepare,mark,token,report){
    report=report||function(m,e){window.rpcToast(m,e);};
    var w=null;
    // Open the tab during the click itself so the browser does not block it as a pop-up.
    try{w=window.open('','_blank');}catch(e){w=null;}
    if(w){try{w.opener=null;w.document.title='Membuka WhatsApp\u2026';w.document.body.innerHTML='<p style="font-family:sans-serif;padding:24px">Membuka WhatsApp\u2026</p>';}catch(e){}}
    report('Menyiapkan pesan WhatsApp\u2026',false);
    window.google.script.run.withSuccessHandler(function(j){
      if(!j.ready||!j.wa_url){if(w)w.close();report('Isi dan simpan Alamat website publik di halaman panitia terlebih dahulu agar link bisa dibuka tamu.',true);return;}
      if(w)w.location.href=j.wa_url;else window.location.href=j.wa_url;
      window.google.script.run.withSuccessHandler(function(){report('WhatsApp dibuka untuk '+j.nama+'. Tekan Kirim di WhatsApp.',false,true);})
        .withFailureHandler(function(e){report(e.message,true);})[mark](token);
    }).withFailureHandler(function(e){if(w)w.close();report(e.message,true);})[prepare](token);
  };
})();
