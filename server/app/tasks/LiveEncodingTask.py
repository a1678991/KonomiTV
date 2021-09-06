
import os
import subprocess
import threading
import time

from app.constants import CONFIG, LIBRARY_PATH, QUALITY
from app.models import Channels
from app.models import LiveStream
from app.models import Programs
from app.utils import Logging
from app.utils import RunAwait
from app.utils.EDCB import EDCBUtil, CtrlCmdUtil


class LiveEncodingTask():


    def __init__(self):

        # エンコーダーの最大再起動回数
        # この数を超えた場合はエンコーダーを再起動しない（無限ループ避け）
        self.max_retry_count = 5  # 5 回まで


    def buildFFmpegOptions(self, quality:str, is_dualmono:bool=False) -> list:
        """
        FFmpeg に渡すオプションを組み立てる

        Args:
            quality (str): 映像の品質 (1080p ~ 240p)
            is_dualmono (bool, optional): 放送がデュアルモノかどうか

        Returns:
            list: FFmpeg に渡すオプションが連なる配列
        """

        # オプションの入る配列
        options = []

        # 入力
        ## -analyzeduration をつけることで、ストリームの分析時間を短縮できる
        options.append('-f mpegts -analyzeduration 500000 -i pipe:0')

        # ストリームのマッピング
        # 主音声・副音声両方をエンコード後の TS に含む（将来の音声切替対応へ準備）

        ## 通常放送・音声多重放送向け
        ## 副音声が検出できない場合にエラーにならないよう、? をつけておく
        if is_dualmono is False:
            options.append('-map 0:v:0 -map 0:a:0 -map 0:a:1? -map 0:s? -map 0:d? -scodec copy -ignore_unknown')

        ## デュアルモノ向け（Lが主音声・Rが副音声）
        else:
            ## 1440x1080 と 1920x1080 が混在しているので、1080p だけリサイズする解像度を特殊な設定に
            scale = 'scale=-2:1080' if quality == '1080p' else f'scale={QUALITY[quality]["width"]}:{QUALITY[quality]["height"]}'
            # 参考: https://github.com/l3tnun/EPGStation/blob/master/config/enc3.js
            # -filter_complex を使うと -vf や -af が使えなくなるため、デュアルモノのみ -filter_complex に -vf や -af の内容も入れる
            options.append(f'-filter_complex yadif=0:-1:1,{scale};volume=2.0,channelsplit[FL][FR]')
            ## Lを主音声に、Rを副音声にマッピング
            options.append('-map 0:v:0 -map [FL] -map [FR] -map 0:s? -map 0:d? -scodec copy -ignore_unknown')

        # フラグ
        ## 主に FFmpeg の起動を高速化するための設定
        options.append('-fflags nobuffer -flags low_delay -max_delay 250000 -max_interleave_delta 1 -threads auto')

        # 映像
        options.append(f'-vcodec libx264 -flags +cgop -vb {QUALITY[quality]["video_bitrate"]} -maxrate {QUALITY[quality]["video_bitrate_max"]}')
        options.append('-aspect 16:9 -r 30000/1001 -g 15 -preset veryfast -profile:v main')
        if is_dualmono is False:  # デュアルモノ以外
            ## 1440x1080 と 1920x1080 が混在しているので、1080p だけリサイズする解像度を特殊な設定に
            if quality == '1080p':
                options.append('-vf yadif=0:-1:1,scale=-2:1080')
            else:
                options.append(f'-vf yadif=0:-1:1,scale={QUALITY[quality]["width"]}:{QUALITY[quality]["height"]}')

        # 音声
        options.append(f'-acodec aac -ac 2 -ab {QUALITY[quality]["audio_bitrate"]} -ar 48000')
        if is_dualmono is False:  # デュアルモノ以外
            options.append('-af volume=2.0')

        # 出力
        options.append('-y -f mpegts')  # MPEG-TS 出力ということを明示
        options.append('pipe:1')  # 標準入力へ出力

        # オプションをスペースで区切って配列にする
        result = []
        for option in options:
            result += option.split(' ')

        return result


    def buildHWEncCOptions(self, encoder_type:str, quality:str, is_dualmono:bool=False) -> list:
        """
        QSVEncC・NVEncC・VCEEncC (便宜上 HWEncC と総称) に渡すオプションを組み立てる

        Args:
            encoder_type (str): エンコーダー (QSVEncC or NVEncC or VCEEncC)
            quality (str): 映像の品質 (1080p ~ 240p)
            is_dualmono (bool, optional): 放送がデュアルモノかどうか

        Returns:
            list: HWEncC に渡すオプションが連なる配列
        """

        # オプションの入る配列
        options = []

        # 入力
        ## --input-probesize, --input-analyze をつけることで、ストリームの分析時間を短縮できる
        ## 両方つけるのが重要で、--input-analyze だけだとエンコーダーがフリーズすることがある
        options.append('--input-format mpegts --fps 30000/1001 --input-probesize 1000K --input-analyze 0.7 --input -')
        ## VCEEncC の HW デコーダーはエラー耐性が低く TS を扱う用途では不安定なので、SW デコーダーを利用する
        if encoder_type == 'VCEEncC':
            options.append('--avsw')
        ## QSVEncC・NVEncC は HW デコーダーを利用する
        else:
            options.append('--avhw')

        # ストリームのマッピング
        # 主音声・副音声両方をエンコード後の TS に含む（将来の音声切替対応へ準備）
        if is_dualmono is False:
            ## 通常放送・音声多重放送向け
            ## 5.1ch 自体は再生可能だが、そのままエンコードするとコケる事があるし、
            ## 何より 5.1ch 非対応な PC だと音がシャリシャリになって聞けたものではないのでステレオで固定する
            options.append('--audio-stream 1?:stereo --audio-stream 2?:stereo --sub-copy asdata --data-copy timed_id3')
        else:
            ## デュアルモノ向け（Lが主音声・Rが副音声）
            options.append('--audio-stream FL,FR --sub-copy asdata --data-copy timed_id3')

        # フラグ
        ## 主に HWEncC の起動を高速化するための設定
        options.append('-m fflags:nobuffer -m max_delay:250000 -m max_interleave_delta:1 --output-thread -1 --lowlatency')
        ## その他の設定
        options.append('--avsync forcecfr --max-procfps 60 --log-level debug')

        # 映像
        options.append(f'--vbr {QUALITY[quality]["video_bitrate"]} --max-bitrate {QUALITY[quality]["video_bitrate_max"]}')
        options.append(f'--dar 16:9 --gop-len 15 --profile main --interlace tff')
        ## インターレース解除
        if encoder_type == 'QSVEncC' or encoder_type == 'NVEncC':
            options.append('--vpp-deinterlace normal')
        elif encoder_type == 'VCEEncC':
            options.append('--vpp-afs preset=default')
        ## プリセット
        if encoder_type == 'QSVEncC':
            options.append('--quality balanced')
        elif encoder_type == 'NVEncC':
            options.append('--preset default')
        elif encoder_type == 'VCEEncC':
            options.append('--preset balanced')
        ## 1440x1080 と 1920x1080 が混在しているので、1080p だけリサイズする解像度を指定しない
        ## TODO: 本当は --output-res -2x1080 を使いたいのだが、なぜか 1080x1080 の扱いになってしまうため保留
        if quality != '1080p':
            options.append(f'--output-res {QUALITY[quality]["width"]}x{QUALITY[quality]["height"]}')

        # 音声
        options.append(f'--audio-codec aac --audio-bitrate {QUALITY[quality]["audio_bitrate"]} --audio-samplerate 48000')
        options.append('--audio-filter volume=2.0 --audio-ignore-decode-error 30')

        # 出力
        options.append('--output-format mpegts')  # MPEG-TS 出力ということを明示
        options.append('--output -')  # 標準入力へ出力

        # オプションをスペースで区切って配列にする
        result = []
        for option in options:
            result += option.split(' ')

        return result


    def run(self, channel_id:str, quality:str) -> None:
        """
        エンコードタスクを実行する

        Args:
            channel_id (str): チャンネルID
            quality (str): 映像の品質 (1080p ~ 240p)
        """

        # ライブストリームのインスタンスを取得する
        livestream = LiveStream(channel_id, quality)

        # まだ Standby になっていなければ、ステータスを Standby に設定
        # 基本はエンコードタスクの呼び出し元である livestream.connect() の方で Standby に設定されるが、再起動の場合はそこを経由しないため必要
        if not (livestream.getStatus()['status'] == 'Standby' and livestream.getStatus()['detail'] == 'エンコーダーを起動しています…'):
            livestream.setStatus('Standby', 'エンコーダーを起動しています…')

        # チャンネル情報からサービス ID とネットワーク ID を取得する
        channel:Channels = RunAwait(Channels.filter(channel_id=channel_id).first())
        service_id = channel.service_id
        network_id = channel.network_id

        # 現在の番組情報を取得する
        program_present:Programs = RunAwait(channel.getCurrentAndNextProgram())[0]

        ## 番組情報が取得できなければ（放送休止中など）ここで Offline にして中断する
        if program_present is None:
            time.sleep(0.5)  # ちょっと待つのがポイント
            livestream.setStatus('Offline', 'この時間は放送を休止しています。')
            return
        Logging.info(f'LiveStream:{livestream.livestream_id} Title:{program_present.title}')

        ## 画質が 480i なのに 1080p にしてもしょうがないので、指定された画質が 480p 以上なら 480p に固定する
        if program_present.video_resolution == '480i' and int(quality[:-1]) > 480:
            quality = '480p'

        if CONFIG['general']['backend'] == 'EDCB':
            cmd = CtrlCmdUtil()
            if CONFIG['general']['edcb_host']:
                cmd.setNWSetting(CONFIG['general']['edcb_host'], CONFIG['general']['edcb_port'])
            set_ch_info = {}
            # これを False にすれば起動確認とプロセス ID の取得ができる
            set_ch_info['use_sid'] = True
            set_ch_info['onid'] = network_id
            set_ch_info['tsid'] = channel.transport_stream_id
            set_ch_info['sid'] = service_id
            set_ch_info['use_bon_ch'] = True
            # NetworkTV モードのチューナーを識別する任意の整数
            # ほかのロケフリ系アプリと重複しないように増分してある
            nwtv_id = livestream.int_id + 500
            set_ch_info['space_or_id'] = nwtv_id
            # TCP 送信を有効にする
            set_ch_info['ch_or_mode'] = 2
            # 起動または同一 ID のチャンネル変更
            nwtv_path = None
            # ほかのタスクがチューナーを閉じている (Idling -> Offline) などで空きがない場合があるのでいくらかリトライする
            set_ch_timeout = time.monotonic() + 5
            while True:
                nwtv_process_id = RunAwait(cmd.sendNwTVIDSetCh(set_ch_info))
                if nwtv_process_id is not None or time.monotonic() >= set_ch_timeout:
                    break
                time.sleep(0.5)
            if nwtv_process_id is None:
                # 失敗。成功時は sendNwTVIDClose() するか予約などに割り込まれるまで起動しつづけるので注意
                nwtv_id = None
            else:
                nwtv_path = RunAwait(EDCBUtil.findNwTVStreamPath(nwtv_id, nwtv_process_id))
                # 少し古い (2021 年 6 月以前) EDCB はパイプの待ち受け再開に時間がかかるので少し待つとよい
                # time.sleep(2)
            if nwtv_path is None:
                # 失敗だがこの後どうすればいいか知らないので開けなさそうな名前を入れておく
                nwtv_path = '__error__'

            ast = subprocess.Popen(
                [LIBRARY_PATH['arib-subtitle-timedmetadater'], '-i', nwtv_path],
                stdout = subprocess.PIPE,  # FFmpeg に繋ぐ
                creationflags = (subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),  # conhost を開かない
            )

        else:
            # Mirakurun 形式のサービス ID
            # NID と SID を 5 桁でゼロ埋めした上で int に変換する
            mirakurun_service_id = int(str(network_id).zfill(5) + str(service_id).zfill(5))
            # Mirakurun API の URL を作成
            mirakurun_stream_api_url = f'{CONFIG["general"]["mirakurun_url"]}/api/services/{mirakurun_service_id}/stream'

            # ***** arib-subtitle-timedmetadater プロセスの作成と実行 *****

            # arib-subtitle-timedmetadater
            ## プロセスを非同期で作成・実行
            ast = subprocess.Popen(
                [LIBRARY_PATH['arib-subtitle-timedmetadater'], '--http', mirakurun_stream_api_url],
                stdout=subprocess.PIPE,  # FFmpeg に繋ぐ
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),  # conhost を開かない
            )

        # ***** エンコーダープロセスの作成と実行 *****

        # エンコーダーの種類を取得
        encoder_type = CONFIG['livestream']['encoder']

        # FFmpeg
        if encoder_type == 'FFmpeg':

            # オプションを取得
            # 現在放送中の番組がデュアルモノの場合、デュアルモノ用のエンコードオプションを取得
            if program_present.primary_audio_type == '1/0+1/0モード(デュアルモノ)':
                encoder_options = self.buildFFmpegOptions(quality, is_dualmono=True)
            else:
                encoder_options = self.buildFFmpegOptions(quality, is_dualmono=False)
            Logging.info(f'LiveStream:{livestream.livestream_id} FFmpeg Commands:\nffmpeg {" ".join(encoder_options)}')

            # プロセスを非同期で作成・実行
            encoder = subprocess.Popen(
                [LIBRARY_PATH['FFmpeg']] + encoder_options,
                stdin=ast.stdout,  # arib-subtitle-timedmetadater からの入力
                stdout=subprocess.PIPE,  # ストリーム出力
                stderr=subprocess.PIPE,  # ログ出力
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),  # conhost を開かない
            )

        # HWEncC
        elif encoder_type == 'QSVEncC' or encoder_type == 'NVEncC' or encoder_type == 'VCEEncC':

            # オプションを取得
            # 現在放送中の番組がデュアルモノの場合、デュアルモノ用のエンコードオプションを取得
            if program_present.primary_audio_type == '1/0+1/0モード(デュアルモノ)':
                encoder_options = self.buildHWEncCOptions(encoder_type, quality, is_dualmono=True)
            else:
                encoder_options = self.buildHWEncCOptions(encoder_type, quality, is_dualmono=False)
            Logging.info(f'LiveStream:{livestream.livestream_id} {encoder_type} Commands:\n{encoder_type} {" ".join(encoder_options)}')

            # プロセスを非同期で作成・実行
            encoder = subprocess.Popen(
                [LIBRARY_PATH[encoder_type]] + encoder_options,
                stdin=ast.stdout,  # arib-subtitle-timedmetadater からの入力
                stdout=subprocess.PIPE,  # ストリーム出力
                stderr=subprocess.PIPE,  # ログ出力
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0),  # conhost を開かない
            )

        # ***** エンコーダーの出力の書き込み *****

        def writer():

            # 非同期でエンコーダーから受けた出力を随時 Queue に書き込む
            while True:

                # エンコーダーの出力をライブストリームに書き込む
                # R/W バッファ: 188B (TS Packet Size) * 256 = 48128B
                livestream.write(encoder.stdout.read(48128))

                # エンコーダープロセスが終了していたらループを抜ける
                if encoder.poll() is not None:

                    # ループを抜ける前に、接続している全てのクライアントの Queue にライブストリームの終了を知らせる None を書き込む
                    # クライアントは None を受信した場合、ストリーミングを終了するようになっている
                    # これがないとクライアントはライブストリームが終了した事に気づかず、Queue を取り出そうとしてずっとブロッキングされてしまう
                    for client in livestream.clients:
                        if client is not None:
                            client.queue.put(None)

                    # この時点で全てのクライアントの接続が切断されているので、クライアントが入るリストをクリアする
                    livestream.clients = list()

                    # ループを抜ける
                    break

        # スレッドを開始
        thread = threading.Thread(target=writer, name='LiveEncodingTask-Writer')
        thread.start()

        # ***** エンコーダーの出力監視と制御 *****

        # エンコード終了後にエンコードタスクを再起動すべきかのフラグ
        is_restart_required = False

        # エンコーダーの出力結果を取得
        line:str = str()
        lines:list = list()
        linebuffer:bytes = bytes()
        while True:

            # ライブストリームのステータスを取得
            livestream_status = livestream.getStatus()

            # 1バイトずつ読み込む
            buffer:bytes = encoder.stderr.read(1)
            if buffer:  # データがあれば

                # 行バッファに追加
                linebuffer = linebuffer + buffer

                # 画面更新 or 改行があれば
                linebreak = b'\r' if os.name == 'nt' else b'\n'
                if (b'\r' in buffer) or (linebreak in buffer):

                    # 行（文字列）を取得
                    try:
                        # 余計な改行や空白を削除
                        # インデントが消えるので見栄えは悪いけど、プログラムで扱う分にはちょうどいい
                        line = linebuffer.decode('utf-8').strip()
                    # UnicodeDecodeError は握りつぶす（どっちみちチャンネル名とか解読できないし）
                    except UnicodeDecodeError:
                        pass

                    # リストに追加
                    lines.append(line)

                    # 行バッファを消去
                    linebuffer = bytes()

                    # ストリーム関連のログを表示
                    if 'Stream #0:' in line:
                        Logging.debug_simple(line)

                    # エンコードの進捗を判定し、ステータスを更新する
                    # 誤作動防止のため、ステータスが Standby の間のみ更新できるようにする
                    if livestream_status['status'] == 'Standby':
                        # FFmpeg
                        if encoder_type == 'FFmpeg':
                            if 'libpostproc    55.  9.100 / 55.  9.100' in line:
                                livestream.setStatus('Standby', 'チューナーを開いています…')
                            elif 'arib parser was created' in line or 'Invalid frame dimensions 0x0.' in line:
                                livestream.setStatus('Standby', 'エンコードを開始しています…')
                            elif 'frame=    1 fps=0.0 q=0.0' in line:
                                livestream.setStatus('Standby', 'バッファリングしています…')
                            elif 'frame=' in line:
                                livestream.setStatus('ONAir', 'ライブストリームは ONAir です。')
                        ## HWEncC
                        elif encoder_type == 'QSVEncC' or encoder_type == 'NVEncC' or encoder_type == 'VCEEncC':
                            if 'input source set to stdin.' in line:
                                livestream.setStatus('Standby', 'チューナーを開いています…')
                            elif 'opened file "pipe:0"' in line:
                                livestream.setStatus('Standby', 'エンコードを開始しています…')
                            elif 'starting output thread...' in line:
                                livestream.setStatus('Standby', 'バッファリングしています…')
                            elif 'Encode Thread:' in line:
                                livestream.setStatus('Standby', 'バッファリングしています…')
                            elif ' frames: ' in line:
                                livestream.setStatus('ONAir', 'ライブストリームは ONAir です。')

            # 現在放送中の番組が終了した時
            if program_present is not None and time.time() > program_present.end_time.timestamp():

                # 次の番組情報を取得する
                program_following:Programs = RunAwait(channel.getCurrentAndNextProgram())[0]

                # 次の番組が None でない
                if program_following is not None:

                    # 現在:デュアルモノ以外 → 次:デュアルモノ
                    if (program_present.primary_audio_type != '1/0+1/0モード(デュアルモノ)') and \
                       (program_following.primary_audio_type == '1/0+1/0モード(デュアルモノ)'):
                        # エンコーダーの音声出力をデュアルモノ対応にするため、エンコーダーを再起動する
                        is_restart_required = True
                        livestream.setStatus('Restart', '音声をデュアルモノに切り替えています…')
                        break

                    # 現在:デュアルモノ → 次:デュアルモノ以外
                    if (program_present.primary_audio_type == '1/0+1/0モード(デュアルモノ)') and \
                       (program_following.primary_audio_type != '1/0+1/0モード(デュアルモノ)'):
                        # エンコーダーの音声出力をステレオ対応にするため、エンコーダーを再起動する
                        is_restart_required = True
                        livestream.setStatus('Restart', '音声をステレオに切り替えています…')
                        break

                    Logging.info(f'LiveStream:{livestream.livestream_id} Title:{program_following.title}')

                # 次の番組情報を現在の番組情報にコピー
                program_present = program_following
                del program_following

            # 現在 ONAir でかつクライアント数が 0 なら Idling（アイドリング状態）に移行
            if livestream_status['status'] == 'ONAir' and livestream_status['clients_count'] == 0:
                livestream.setStatus('Idling', 'ライブストリームは Idling です。')

            # 現在 Idling でかつ最終更新から指定された秒数以上経っていたらエンコーダーを終了し、Offline 状態に移行
            if (livestream_status['status'] == 'Idling') and \
               (time.time() - livestream_status['updated_at'] > CONFIG['livestream']['max_alive_time']):
                livestream.setStatus('Offline', 'ライブストリームは Offline です。')
                break

            # 現在 ONAir でかつストリームデータの最終書き込み時刻から 2 秒以上が経過しているなら、エンコーダーがフリーズしたものとみなす
            # 何らかの理由でエンコードが途中で停止した場合、livestream.write() が実行されなくなるのを利用する
            # ステータスを Restart に設定し、エンコードタスクを再起動する
            if livestream_status['status'] == 'ONAir' and time.time() - livestream.stream_data_writed_at > 2:
                is_restart_required = True  # エンコーダーの再起動を要求
                livestream.setStatus('Restart', 'エンコードが途中で停止しました。ライブストリームを再起動します。')
                # 直近 30 件のログを表示
                for log in lines[-31:-1]:
                    Logging.warning(log)
                break

            # すでに Offline 状態になっている場合、エンコーダーを終了する
            # エンコードタスク以外から Offline 状態に設定される事も考えられるため
            if livestream_status['status'] == 'Offline':
                break

            # 特定のエラーログが出力されている場合は回復が見込めないため、エンコーダーを終了する
            # エンコーダーを再起動することで回復が期待できる場合は、ステータスを Restart に設定しエンコードタスクを再起動する
            ## FFmpeg
            if encoder_type == 'FFmpeg':
                if 'Stream map \'0:v:0\' matches no streams.' in line:
                    # 主にチューナー不足が原因のエラーのため、エンコーダーの再起動は行わない
                    livestream.setStatus('Offline', 'チューナー不足のため、ライブストリームを開始できません。')
                    break
                elif 'Conversion failed!' in line:
                    # 捕捉されないエラー
                    is_restart_required = True  # エンコーダーの再起動を要求
                    livestream.setStatus('Restart', 'エンコード中に予期しないエラーが発生しました。ライブストリームを再起動します。')
                    # 直近 30 件のログを表示
                    for log in lines[-31:-1]:
                        Logging.warning(log)
                    break
            ## HWEncC
            elif encoder_type == 'QSVEncC' or encoder_type == 'NVEncC' or encoder_type == 'VCEEncC':
                if 'error finding stream information.' in line:
                    # 主にチューナー不足が原因のエラーのため、エンコーダーの再起動は行わない
                    livestream.setStatus('Offline', 'チューナー不足のため、ライブストリームを開始できません。')
                    break
                elif 'due to the NVIDIA\'s driver limitation.' in line:
                    # NVEncC で、同時にエンコードできるセッション数 (Geforceだと3つ) を全て使い果たしている時のエラー
                    livestream.setStatus('Offline', 'NVENC のエンコードセッションが不足しているため、ライブストリームを開始できません。')
                    break
                elif 'avqsv: codec h264(yuv420p) unable to decode by qsv.' in line:
                    # QSVEncC 非対応の環境
                    livestream.setStatus('Offline', 'QSVEncC 非対応の環境のため、ライブストリームを開始できません。')
                    break
                elif 'CUDA not available.' in line:
                    # NVEncC 非対応の環境
                    livestream.setStatus('Offline', 'NVEncC 非対応の環境のため、ライブストリームを開始できません。')
                    break
                elif 'Failed to initalize VCE factory:' in line:
                    # VCEEncC 非対応の環境
                    livestream.setStatus('Offline', 'VCEEncC 非対応の環境のため、ライブストリームを開始できません。')
                    break
                elif 'Consider increasing the value for the --input-analyze and/or --input-probesize!' in line:
                    # --input-probesize or --input-analyze の期間内に入力ストリームの解析が終わらなかった
                    is_restart_required = True  # エンコーダーの再起動を要求
                    livestream.setStatus('Restart', '入力ストリームの解析に失敗しました。ライブストリームを再起動します。')
                    break
                elif 'finished with error!' in line:
                    # 捕捉されないエラー
                    is_restart_required = True  # エンコーダーの再起動を要求
                    livestream.setStatus('Restart', 'エンコード中に予期しないエラーが発生しました。ライブストリームを再起動します。')
                    # 直近 50 件のログを表示 (最後の方 80 件はデバッグログなので除外)
                    for log in lines[-130:-80]:
                        Logging.warning(log)
                    break

            # エンコーダーが意図せず終了した場合、エンコーダーを（明示的に）終了する
            if not buffer and encoder.poll() is not None:
                # エンコーダーの再起動を要求
                is_restart_required = True
                # エンコーダーの再起動前提のため、あえて Offline にはせず Restart とする
                livestream.setStatus('Restart', 'エンコーダーが強制終了されました。ライブストリームを再起動します。')
                if encoder_type == 'FFmpeg':
                    # 直近 30 件のログを表示
                    for log in lines[-31:-1]:
                        Logging.warning(log)
                    break
                # HWEncC はログを詳細にハンドリングするためにログレベルを debug に設定しているため、FFmpeg よりもログが圧倒的に多い
                elif encoder_type == 'QSVEncC' or encoder_type == 'NVEncC' or encoder_type == 'VCEEncC':
                    # 直近 50 件のログを表示 (最後の方 80 件はデバッグログなので除外)
                    for log in lines[-130:-80]:
                        Logging.warning(log)
                    break
                break

        # ***** エンコード終了後の処理 *****

        # 明示的にプロセスを終了する
        ast.kill()
        encoder.kill()

        if CONFIG['general']['backend'] == 'EDCB':
            if nwtv_id is not None:
                # ここで閉じずに次のタスクにうまく引き継げば再利用もできるはず
                RunAwait(cmd.sendNwTVIDClose(nwtv_id))

        # エンコードタスクを再起動する（エンコーダーの再起動が必要な場合）
        if is_restart_required is True:

            # 最大再起動回数が 0 より上であれば
            if self.max_retry_count > 0:
                time.sleep(0.1)  # 少し待つ
                self.max_retry_count = self.max_retry_count - 1  # カウントを減らす
                self.run(channel_id, quality)  # 新しいタスクを立ち上げる

            # 最大再起動回数を使い果たしたので、Offline にする
            else:
                livestream.setStatus('Offline', 'ライブストリームの再起動に失敗しました。')
