import{d as a,m as o,U as p,q as h,_ as l,b as c,e as d,h as m}from"./index-bUaC_4Uu.js";import{W as u,P as _}from"./PlayerController-BMe6OGiI.js";import{u as f}from"./ChannelsStore-D5BRmfP6.js";import{u as y}from"./CaptureManager-CV83zEIS.js";import"./ssrBoot-Bj_AfeK6.js";import"./VDialog-CUlQ0LC3.js";import"./VChip-Dh-kTiWs.js";import"./VTextField-CXO-vs30.js";import"./VAvatar-DsPtSKoG.js";import"./VCard-CW3hdVCD.js";import"./VSlider-Dn1xAUnE.js";import"./swiper-qjhCv3JO.js";import"./CommentMuteSettings-CHegJy2t.js";import"./VSelect-Dn8NbAia.js";import"./vuedraggable.umd-BA_C9-M9.js";import"./Twitter-DMC4fmqM.js";import"./PlayerUtils-DvsnJsyj.js";let e=null;const S=a({name:"TV-Watch",components:{Watch:u},data(){return{interval_ids:[]}},computed:{...o(f,y,h)},created(){this.channelsStore.display_channel_id=this.$route.params.display_channel_id,this.init()},beforeRouteUpdate(t,n,s){const i=this.destroy();this.channelsStore.display_channel_id=t.params.display_channel_id,(async()=>this.playerStore.is_zapping===!0?(this.playerStore.is_zapping=!1,this.interval_ids.push(window.setTimeout(()=>{i.then(()=>this.init())},.5*1e3))):i.then(()=>this.init()))(),s()},beforeUnmount(){this.destroy(),this.channelsStore.display_channel_id="gr000"},methods:{async init(){const t=60-new Date().getSeconds();if(this.interval_ids.push(window.setTimeout(()=>{this.channelsStore.update(!0),this.interval_ids.push(window.setInterval(()=>{this.channelsStore.update(!0)},30*1e3))},t*1e3)),await this.channelsStore.update(),this.$route.params.display_channel_id===void 0){this.$router.push({path:"/not-found/"});return}if(this.channelsStore.channel.current.name==="チャンネル情報取得エラー"){await p.sleep(3),this.$router.push({path:"/not-found/"});return}e=new _("Live"),await e.init()},async destroy(){for(const t of this.interval_ids)window.clearInterval(t);this.interval_ids=[],e!==null&&(await e.destroy(),e=null)}}});function w(t,n,s,i,v,$){const r=c("Watch",!0);return d(),m(r,{playback_mode:"Live"})}const j=l(S,[["render",w]]);export{j as default};