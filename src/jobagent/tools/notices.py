"""Dismiss a proved cookie overlay through its own ordinary click handler."""
import re

_FORWARD=re.compile(r'''\s*document\.getElementById\((['"])truste-consent-button\1\)\.click\(\);?\s*''')
_BLOCKING=r'''e=>{
 if(!e.isConnected || getComputedStyle(e).pointerEvents==='none')return false;
 const r=e.getBoundingClientRect();if(!r.width||!r.height)return false;
 const top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
 return top===e || !!top&&e.contains(top);
}'''


def dismiss_cookie_overlay(page,frames=None):
    """Handle only TrustArc's native forwarding overlay; never remove DOM or solve a challenge."""
    for frame in (frames if frames is not None else getattr(page,'frames',())):
        try:
            overlays=frame.locator('#consent_blackbar[role=dialog][aria-label="consent banner"] #trustarc-banner-overlay[onclick]').all()
        except Exception:
            continue          # a frame that detaches while the page navigates (a sign-up's Continue) is not an error worth stopping a run
        for overlay in overlays:
            if not _FORWARD.fullmatch(overlay.get_attribute('onclick') or ''):continue
            root=overlay.locator('xpath=ancestor::*[@id="consent_blackbar"][1]')
            button=root.locator('#truste-consent-button')
            if button.count()!=1 or button.is_disabled() or not overlay.evaluate(_BLOCKING):continue
            # An ordinary pointer click runs the site's published dismissal.
            # Opacity can be zero while this overlay still intercepts all input.
            element=overlay.element_handle()
            overlay.click(timeout=3000)
            frame.wait_for_function('(e)=>!('+_BLOCKING+')(e)',arg=element,timeout=3000)
            return True
    return False


COOKIE_DIALOG = re.compile(r"(?i)cookie (?:preference|setting)s?|manage cookies")
PRIVACY_DIALOG = re.compile(r"(?i)privacy (?:policy|statement|notice)")
DISABLE_ALL = re.compile(r"^\s*(?:disable all|reject all|decline all|deny all|only (?:necessary|essential)|necessary only)\s*$", re.I)
SAVE_BUTTON = re.compile(r"^\s*(?:save(?: (?:and )?(?:continue|preferences|settings))?|confirm(?: my choices)?|accept|agree|ok|done|continue)\s*$", re.I)
AGREE_BOX = re.compile(r"(?i)\b(?:i )?(?:agree|accept|acknowledge|have read)\b")


def resolve_blocking_dialogs(page, privacy_ok: bool = False, log=lambda m: None) -> int:
    """Open dialogs that sit over an application form and gate it (Dayforce: a Cookie Preferences dialog under an Amphenol Privacy
    Policy dialog). A cookie dialog gets its most private choice (Disable All, then Save). A privacy-policy dialog with an 'I agree'
    box is agreed to ONLY when `privacy_ok` (the candidate's profile says yes to privacy-notice acknowledgements); any other agreement
    is left for a person. Never clicks Submit or Apply. Returns how many dialogs were closed."""
    done = 0
    for _ in range(4):
        progressed = False
        dialogs = page.locator('[role=dialog], [aria-modal=true], .ant-modal').filter(visible=True)
        for i in range(dialogs.count() - 1, -1, -1):          # the last one in the document is normally the top one
            d = dialogs.nth(i)
            try:
                text = (d.inner_text(timeout=2000) or "")[:3000]
                if PRIVACY_DIALOG.search(text[:300]) and d.get_by_role("checkbox").count():
                    box = d.get_by_role("checkbox").filter(visible=True).first
                    label = (box.evaluate("e => (e.labels && e.labels[0] ? e.labels[0].innerText : '') || e.closest('label')?.innerText || ''") or "")
                    if not AGREE_BOX.search(label) or not privacy_ok:
                        log("dialog: a privacy agreement is open and no standing owner consent covers it: left for the candidate")
                        continue
                    if not box.is_checked():
                        box.check(timeout=3000)
                    d.get_by_role("button", name=SAVE_BUTTON).filter(visible=True).first.click(timeout=3000)
                    log("dialog: agreed to the privacy statement (owner's standing yes) and saved")
                elif COOKIE_DIALOG.search(text[:200]):
                    off = d.get_by_role("button", name=DISABLE_ALL).filter(visible=True)
                    if off.count():
                        off.first.click(timeout=3000)
                    d.get_by_role("button", name=SAVE_BUTTON).filter(visible=True).first.click(timeout=3000)
                    log("dialog: cookie preferences saved with optional cookies disabled")
                else:
                    continue
                page.wait_for_timeout(700)
                done += 1
                progressed = True
                break
            except Exception as exc:
                log(f"dialog: could not resolve it ({type(exc).__name__}: {str(exc).splitlines()[0][:120]})")
                continue
        if not progressed:
            break
    return done
