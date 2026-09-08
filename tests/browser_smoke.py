"""Real browser interaction against isolated generated fixtures, never user images."""
import json
import math
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
from gallery.app import create_app


def main():
    results = Path(__file__).resolve().parents[1] / 'test-results'
    results.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='browser-', dir=results))
    public = root / 'public'
    public.mkdir()
    palettes = [('#e6d6bc','#ce9470','#547268'),('#c9d8d4','#ebccb0','#657f82'),('#e7ddc9','#bd6b4b','#8f987b'),('#c8cdd7','#ede1ce','#727c9c')]
    for i in range(18):
        bg, sun, hills = palettes[i % 4]
        width, height = (900, 1200) if i % 3 == 0 else (1200, 850)
        image = Image.new('RGB', (width, height), bg)
        draw = ImageDraw.Draw(image)
        cx, cy = width * (.3 + .13 * (i % 4)), height * .3
        radius = width * .13
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius), fill=sun)
        for j in range(3):
            points=[(0,height)] + [(x, int(height * (.6+j*.14) + math.sin(x/width*5+i+j)*height*.08)) for x in range(0,width+1,10)] + [(width,height)]
            draw.polygon(points, fill=hills if j!=1 else sun)
        folder = public if i % 3 == 0 else public / ['Landscape','Artwork','Travel'][i % 3]
        folder.mkdir(exist_ok=True)
        path = folder / f'{i+1:02d}_静かな風景.jpg'
        image.save(path, quality=90)
        os.utime(path, (time.time()-10-i,time.time()-10-i))
    app=create_app(root / 'data',public,start_scanner=False)
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning',proxy_headers=False,access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    errors=[]
    credentials={'username':'browser-fixture','password':'browser-fixture-only-2026'}
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch()
            desktop=browser.new_context(viewport={'width':1440,'height':1040},device_scale_factor=1)
            page=desktop.new_page()
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://localhost:{port}')
            expect(page.locator('#username')).to_be_visible()
            page.screenshot(path=str(root/'setup-desktop.png'),full_page=True)
            page.locator('#username').fill(credentials['username'])
            page.locator('#password').fill(credentials['password'])
            page.locator('#password-confirm').fill(credentials['password'])
            page.get_by_role('button',name='ライブラリを作成').click()
            expect(page.get_by_role('button',name='ログイン',exact=True)).to_be_visible()
            page.locator('#username').fill(credentials['username'])
            page.locator('#password').fill(credentials['password'])
            page.get_by_role('button',name='ログイン',exact=True).click()
            expect(page.locator('.folder-card')).to_have_count(2)
            expect(page.locator('.image-card')).to_have_count(0)
            page.screenshot(path=str(root/'empty-desktop.png'),full_page=True)
            app.state.library.scan()
            page.reload()
            expect(page.locator('.image-card')).to_have_count(6)
            for img in page.locator('.card-open img').all()[:8]:
                expect(img).to_have_js_property('complete', True)
                assert img.evaluate('(img) => img.naturalWidth > 0')
            page.screenshot(path=str(root/'gallery-desktop.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.locator('#sort-button').click()
            expect(page.locator('#sort-options')).to_be_visible()
            page.locator('[data-sort="name"]').click()
            expect(page.locator('#sort-label')).to_have_text('名前 A → Z')
            page.locator('#sort-button').focus()
            page.keyboard.press('ArrowDown')
            page.keyboard.press('Home')
            page.keyboard.press('Enter')
            expect(page.locator('#sort-label')).to_have_text('更新が新しい順')
            expect(page.locator('#sort-options')).to_be_hidden()
            page.locator('.card-open').first.click()
            expect(page.locator('#viewer')).to_be_visible()
            expect(page.locator('#viewer-loading')).not_to_be_visible()
            first_name=page.locator('#viewer-name').inner_text()
            page.keyboard.press('ArrowRight')
            expect(page.locator('#viewer-name')).not_to_have_text(first_name)
            page.locator('#zoom-in').click()
            expect(page.locator('#zoom-reset')).to_have_text('150%')
            page.locator('#info-toggle').click()
            page.locator('#edit-name').fill('renamed-ui.jpg')
            page.locator('#edit-tags').fill('作品, テスト')
            page.locator('#image-form button[type=submit]').click()
            expect(page.locator('#viewer-name')).to_have_text('renamed-ui.jpg')
            assert list(public.rglob('renamed-ui.jpg'))
            page.screenshot(path=str(root/'viewer-desktop.png'),full_page=True)
            page.locator('#close-viewer').click()
            page.locator('#search').fill('テスト')
            expect(page.locator('.image-card')).to_have_count(1)
            page.locator('#search').fill('')
            expect(page.locator('.image-card')).to_have_count(6)
            page.locator('#new-folder').click()
            page.locator('#folder-name').fill('UIから作成')
            page.locator('#folder-form button[type=submit]').click()
            expect(page.locator('#modal')).not_to_be_visible()
            assert (public/'UIから作成').is_dir()
            page.locator('#select-button').click()
            page.locator('.card-select').nth(0).click()
            page.locator('.card-select').nth(1).click()
            page.locator('#batch-move').click()
            page.locator('#move-folder').select_option('UIから作成')
            page.locator('#batch-form button[type=submit]').click()
            expect(page.locator('#modal')).not_to_be_visible()
            assert len(list((public/'UIから作成').glob('*.jpg')))==2
            page.locator('#settings-button').click()
            page.locator('#whitelist').fill('192.168.0.0/24')
            page.locator('#access-form input[name=enabled]').check()
            page.locator('#access-form button[type=submit]').click()
            expect(page.locator('#toast')).to_contain_text('アクセス設定を保存')
            page.locator('#close-modal').click()

            mobile=browser.new_context(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
            mobile.add_cookies(desktop.cookies())
            phone=mobile.new_page()
            phone.on('pageerror',lambda e:errors.append(str(e)))
            phone.goto(f'http://localhost:{port}')
            expect(phone.locator('.image-card')).to_have_count(4)
            for img in phone.locator('.card-open img').all()[:4]:
                expect(img).to_have_js_property('complete', True)
                assert img.evaluate('(img) => img.naturalWidth > 0')
            assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
            phone.screenshot(path=str(root/'gallery-mobile.png'),full_page=False)
            phone.locator('#sort-button').click()
            expect(phone.locator('#sort-options')).to_be_visible()
            assert phone.locator('#sort-options').evaluate('(el)=>el.getBoundingClientRect().right<=innerWidth')
            phone.screenshot(path=str(root/'sort-mobile.png'))
            phone.locator('[data-sort="newest"]').click()
            phone.locator('#menu-button').click()
            expect(phone.locator('#sidebar')).to_have_class('sidebar open')
            phone.locator('#folder-nav [data-folder="UIから作成"]').click()
            expect(phone.locator('.image-card')).to_have_count(2)
            phone.locator('.card-open').first.click()
            expect(phone.locator('#viewer-loading')).not_to_be_visible()
            phone.locator('#zoom-in').click()
            expect(phone.locator('#zoom-reset')).to_have_text('150%')
            phone.locator('#zoom-reset').click()
            # Dispatch actual touch events through Chromium's input interface.
            cdp=mobile.new_cdp_session(phone)
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':300,'y':350}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':90,'y':355}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            expect(phone.locator('#viewer-position')).to_contain_text('2 / 2')
            cdp.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':140,'y':350,'id':0},{'x':240,'y':350,'id':1}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':90,'y':350,'id':0},{'x':290,'y':350,'id':1}]})
            cdp.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
            expect(phone.locator('#zoom-reset')).not_to_have_text('フィット')
            phone.screenshot(path=str(root/'viewer-mobile.png'),full_page=True)
            phone.locator('#info-toggle').click()
            expect(phone.locator('#edit-tags')).to_be_visible()
            phone.screenshot(path=str(root/'edit-mobile.png'),full_page=True)
            phone.locator('#close-viewer').click()
            # Mobile file picker uploads to the open folder and updates immediately.
            phone.locator('#upload-input').set_input_files({'name':'mobile-upload.png','mimeType':'image/png','buffer':make_upload()})
            expect(phone.locator('#upload-status')).to_contain_text('1件をアップロードしました')
            expect(phone.locator('.image-card')).to_have_count(3)
            assert (public/'UIから作成/mobile-upload.png').is_file()

            # Desktop OS-style drop, then internal image and folder drag/move.
            page.reload()
            expect(page.locator('.image-card')).to_have_count(4)
            transfer=page.evaluate_handle('(bytes) => { const dt=new DataTransfer();dt.items.add(new File([new Uint8Array(bytes)],"desktop-upload.png",{type:"image/png"}));return dt; }',list(make_upload()))
            page.locator('#gallery').dispatch_event('drop',{'dataTransfer':transfer})
            expect(page.locator('#upload-status')).to_contain_text('1件をアップロードしました')
            expect(page.locator('.image-card')).to_have_count(5)
            page.locator('.image-card').filter(has=page.locator('[title="desktop-upload.png"]')).drag_to(page.locator('.folder-card[data-drop-folder="Artwork"]'))
            expect(page.locator('.image-card')).to_have_count(4)
            assert (public/'Artwork/desktop-upload.png').is_file()
            page.locator('.folder-card[data-drag-folder="Travel"]').drag_to(page.locator('.folder-card[data-drop-folder="Artwork"]'))
            expect(page.locator('.folder-card')).to_have_count(2)
            assert (public/'Artwork/Travel').is_dir()
            page.locator('#folder-nav [data-collapse="Artwork"]').click()
            expect(page.locator('#folder-nav [data-folder="Artwork/Travel"]')).to_have_count(0)
            page.locator('#folder-nav [data-collapse="Artwork"]').click()
            expect(page.locator('#folder-nav [data-folder="Artwork/Travel"]')).to_be_visible()
            page.locator('#toggle-folders').click()
            expect(page.locator('#folder-nav')).to_be_hidden()
            page.locator('#toggle-folders').click()
            page.screenshot(path=str(root/'organizer-desktop.png'),full_page=True)
            page.locator('#settings-button').click()
            expect(page.locator('#modal')).to_be_visible()
            assert page.locator('#modal').evaluate('(el)=>el.scrollHeight<=el.clientHeight+1')
            assert page.locator('#modal-body').evaluate('(el)=>el.scrollHeight>el.clientHeight')
            assert page.locator('#modal-body').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
            page.screenshot(path=str(root/'settings-desktop.png'))
            page.locator('#close-modal').click()
            # Nested breadcrumbs and real native video playback use synthetic files only.
            page.locator('#folder-nav [data-folder="Artwork/Travel"]').click()
            expect(page.locator('#breadcrumb')).to_have_text('ライブラリ/Artwork/Travel')
            page.screenshot(path=str(root/'breadcrumb-desktop.png'))
            page.locator('#breadcrumb [data-breadcrumb="Artwork"]').click()
            expect(page.locator('#breadcrumb [aria-current="location"]')).to_have_text('Artwork')
            page.locator('#breadcrumb [data-breadcrumb=""]').click()
            from test_video import make_video
            make_video(public/'clip.mp4')
            app.state.library.scan()
            page.reload()
            page.locator('.image-card').filter(has=page.locator('[title="clip.mp4"]')).locator('.card-open').click()
            player=page.locator('#viewer-video')
            expect(player).to_be_visible()
            expect(page.locator('#viewer-loading')).to_be_hidden()
            assert player.evaluate('(v)=>v.duration') == 3
            player.evaluate('(v)=>v.play()')
            expect(player).to_have_js_property('paused',False)
            player.evaluate('(v)=>{v.pause();v.currentTime=1.5;}')
            expect(player).to_have_js_property('seeking',False)
            assert abs(player.evaluate('(v)=>v.currentTime')-1.5)<.1
            page.screenshot(path=str(root/'video-desktop.png'))
            page.locator('#close-viewer').click()
            expect(player).to_have_js_property('paused',True)
            assert player.get_attribute('src') is None
            phone.reload()
            phone.locator('#menu-button').click()
            phone.locator('#folder-nav [data-folder="Artwork/Travel"]').click()
            expect(phone.locator('#breadcrumb')).to_have_text('ライブラリ/Artwork/Travel')
            assert phone.evaluate('document.documentElement.scrollWidth<=innerWidth')
            expect(phone.locator('#sidebar')).not_to_have_class('sidebar open')
            phone.screenshot(path=str(root/'breadcrumb-mobile.png'),animations='disabled')
            phone.locator('#breadcrumb [data-breadcrumb=""]').click()
            phone.locator('.image-card').filter(has=phone.locator('[title="clip.mp4"]')).locator('.card-open').click()
            expect(phone.locator('#viewer-loading')).to_be_hidden()
            phone.locator('#viewer-video').evaluate('(v)=>v.play()')
            expect(phone.locator('#viewer-video')).to_have_js_property('paused',False)
            phone.screenshot(path=str(root/'video-mobile.png'))
            phone.locator('#close-viewer').click()
            # Exercise the upload button and picker handoff, not just the hidden input.
            with phone.expect_file_chooser() as picker_event:
                phone.locator('#upload-button').click()
            picker=picker_event.value
            assert picker.element.get_attribute('accept') is None
            picker.set_files({'name':'mobile-video.mp4','mimeType':'video/mp4','buffer':(public/'clip.mp4').read_bytes()})
            expect(phone.locator('#upload-status')).to_contain_text('1件をアップロードしました')
            expect(phone.locator('.image-card').filter(has=phone.locator('[title="mobile-video.mp4"]'))).to_have_count(1)
            assert (public/'mobile-video.mp4').read_bytes()==(public/'clip.mp4').read_bytes()
            upload_requests=[]
            phone.on('request',lambda request:upload_requests.append(request.url) if '/api/upload?' in request.url else None)
            with phone.expect_file_chooser() as rejected_picker:
                phone.locator('#upload-button').click()
            rejected_picker.value.set_files([
                {'name':'notes.txt','mimeType':'text/plain','buffer':b'not media'},
                {'name':'pretend.mp4','mimeType':'video/mp4','buffer':b'not a video'},
                {'name':'pretend.png','mimeType':'image/png','buffer':b'not an image'},
                {'name':'vector.svg','mimeType':'image/svg+xml','buffer':b'<svg/>'}])
            expect(phone.locator('#upload-status')).to_contain_text('4件は失敗しました')
            assert upload_requests==[],upload_requests
            phone.locator('#close-modal').click()
            phone.locator('#menu-button').click()
            phone.locator('#logout').click()
            expect(phone.locator('#username')).to_be_visible()
            phone.screenshot(path=str(root/'login-mobile.png'),full_page=True)
            assert not errors, errors
            browser.close()
        report={'result':'PASS','screenshots':str(root),'browser_errors':errors,'checks':['desktop setup/login','folder-scoped thumbnail decodes','keyboard navigation','zoom','rename and tags','search','folder creation','batch filesystem move','IP settings','mobile 390px no overflow','mobile folder navigation','touch swipe','two-finger pinch','mobile edit panel','mobile upload','desktop file drop','image drag move','folder drag move','nested folder collapse','whole tree collapse','contained settings scrollbar','nested desktop/mobile breadcrumbs','MP4 playback and seek','video close stops playback','mobile upload button video picker handoff','unsupported and disguised files rejected before network upload','logout']}
        (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True))
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        sock.close()


def make_upload():
    import io
    buffer=io.BytesIO()
    Image.new('RGB',(80,60),'orange').save(buffer,'PNG')
    return buffer.getvalue()


if __name__=='__main__':
    main()
