/* Sites21: auto-connect the owner's browser key; preserve the existing map bridge.
 * No key input, cookies, URL state, logs or browser-storage writes.
 * Only the official SDK request receives the JavaScript key.
 */
(()=>{'use strict';
const config=window.RENTCHECK_MAP_CONFIG||{};
const button=document.querySelector('#connectKakao');
const status=document.querySelector('#kakaoConnectionStatus');
const summary=document.querySelector('#kakaoConnectionSummary');
const panel=document.querySelector('.kakao-connect');
const badge=document.querySelector('.private');
let latest=null,ready=false,busy=false,started=false,script=null,timer=null,attempt=0;
let phase='idle';
function message(text){if(status)status.textContent=text;}
function restoreFallback(){
  const live=document.querySelector('#kakaoMap'),fallback=document.querySelector('#map');
  if(live)live.hidden=true;
  if(fallback)fallback.style.visibility='';
  document.querySelector('.mapcanvas')?.classList.remove('kakao-connected');
}
function fail(reason='카카오 SDK 요청에 실패했습니다.'){
  ++attempt;clearTimeout(timer);timer=null;
  if(script){script.onload=null;script.onerror=null;script.remove();script=null;}
  busy=false;ready=false;phase='failed';restoreFallback();
  if(panel)panel.hidden=false;
  if(summary)summary.textContent='카카오 연결 확인 필요 · 행정경계 지도 사용';
  if(button){button.disabled=false;button.textContent='지도 연결 다시 시도';}
  if(badge)badge.textContent='검수중 · 보조 지도';
  message(reason+' 네트워크·JavaScript SDK 허용 도메인·지도 API 사용 설정을 확인하세요. 목록·검색·달력은 보조 지도에서 계속 사용할 수 있습니다.');
}
function sync(opts){
  latest=opts;
  if(!ready||document.querySelector('#mapPage')?.hidden)return;
  try{
    const live=document.querySelector('#kakaoMap');
    if(!live||!window.RentKakaoMap?.start||!window.RentKakaoMap?.update)throw Error('map bridge unavailable');
    live.hidden=false;
    if(!started){
      const initialized=window.RentKakaoMap.start({
        container:'#kakaoMap',statusSelector:'#kakaoMapStatus',
        onPick:(id,open,kind)=>latest?.onPick(id,open,kind),
        onMove:c=>latest?.onMove(c)
      });
      if(!initialized)throw Error('map initialization failed');
      started=true;
    }
    window.RentKakaoMap.update(opts);
    document.querySelector('#map').style.visibility='hidden';
    document.querySelector('.mapcanvas')?.classList.add('kakao-connected');
    window.RentKakaoMap.resize?.();
    phase='connected';
    if(panel)panel.hidden=true;
    if(badge)badge.textContent='검수중 · 카카오 연결';
    if(button){button.disabled=true;button.textContent='자동 연결됨';}
    message('카카오 SDK와 지도 초기화 완료. 새로고침해도 설정된 키로 자동 연결합니다.');
  }catch(_){fail('카카오 지도 초기화에 실패했습니다.');}
}
function connect(){
  if(busy||ready)return;
  const key=String(config.kakaoJavaScriptKey||'').trim();
  if(!key){fail('운영자의 카카오 지도 설정 대기 중입니다. 방문자는 키를 입력할 필요가 없습니다.');return;}
  if(!/^[a-f0-9]{32}$/i.test(key)){fail('app/config.public.js의 JavaScript 키 형식을 확인하세요.');return;}
  const current=++attempt;busy=true;phase='connecting';
  if(button)button.disabled=true;
  if(panel)panel.hidden=true;
  if(badge)badge.textContent='검수중 · 지도 연결중';
  message('설정된 JavaScript 키로 카카오 지도를 자동 연결하고 있습니다.');
  const sdkReady=()=>{
    if(current!==attempt)return;
    try{
      if(!window.kakao?.maps?.load){fail('SDK가 지도 로더를 반환하지 않았습니다.');return;}
      window.kakao.maps.load(()=>{
        if(current!==attempt)return;
        clearTimeout(timer);timer=null;busy=false;ready=true;phase='sdk-ready';
        if(script){script.onload=null;script.onerror=null;script.remove();script=null;}
        if(latest)sync(latest);
      });
    }catch(_){fail('카카오 지도 로더 실행에 실패했습니다.');}
  };
  const ms=Number(config.timeoutMs);
  timer=setTimeout(()=>{if(current===attempt)fail('카카오 지도 응답 대기시간이 초과되었습니다.');},Number.isFinite(ms)&&ms>=1000?ms:20000);
  if(window.kakao?.maps?.load){sdkReady();return;}
  script=document.createElement('script');script.id='rentcheck-kakao-sdk';script.async=true;
  script.referrerPolicy='strict-origin-when-cross-origin';
  script.src='https://dapi.kakao.com/v2/maps/sdk.js?appkey='+encodeURIComponent(key)+'&autoload=false&libraries=services';
  script.onerror=()=>{if(current===attempt)fail();};script.onload=sdkReady;
  document.head.append(script);
}
button?.addEventListener('click',connect);
window.RentKakaoSession=Object.freeze({sync,isActive:()=>ready&&started,activeTab:()=>latest?.tab,connect,getStatus:()=>({phase,ready,started,busy})});
// defer script runs after parsing; no button click is required.
if(config.autoConnect!==false)connect();
})();
