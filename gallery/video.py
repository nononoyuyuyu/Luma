"""Local-only video inspection and bounded thumbnail decoding using bundled FFmpeg."""
import math
import av

EXTENSIONS = {'.mp4', '.m4v', '.mov', '.webm'}
OPTIONS = {'protocol_whitelist': 'file', 'format_whitelist': 'mov,matroska,webm',
           'enable_drefs': '0', 'use_absolute_path': '0', 'probesize': '5000000', 'analyzeduration': '5000000'}


def inspect(path, thumbnail=False):
    try:
        with av.open(str(path), options=OPTIONS) as container:
            streams = [s for s in container.streams.video if not (s.disposition & av.stream.Disposition.attached_pic)]
            if not streams:
                raise ValueError('映像トラックがありません。')
            stream = streams[0]
            width, height = stream.width, stream.height
            if width <= 0 or height <= 0 or width * height > 80_000_000:
                raise ValueError('対応できない動画の解像度です。')
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else (container.duration or 0) / av.time_base
            if not math.isfinite(duration) or duration < 0:
                duration = 0
            meta = {'width': width, 'height': height, 'format': 'WEBM' if 'matroska' in container.format.name else 'MP4',
                    'animated': 0, 'kind': 'video', 'duration': duration, 'video_codec': stream.codec_context.name}
            if not thumbnail:
                return meta
            stream.codec_context.thread_count = 2
            # Decode only until the first displayable frame, with a packet budget.
            for index, packet in enumerate(container.demux(stream)):
                if index >= 500:
                    break
                for frame in packet.decode():
                    if frame.width * frame.height > 80_000_000:
                        raise ValueError('対応できない動画の解像度です。')
                    ratio = min(1, 480 / max(frame.width, frame.height))
                    image = frame.reformat(width=max(1, round(frame.width * ratio)), height=max(1, round(frame.height * ratio)), format='rgb24').to_image()
                    rotation = getattr(frame, 'rotation', 0)
                    if rotation:
                        image = image.rotate(rotation, expand=True)
                    return image
            raise ValueError('動画のプレビューを作成できません。')
    except av.FFmpegError as exc:
        raise ValueError('動画を読み込めません。形式・破損を確認してください。') from exc
