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
        for overlay in frame.locator('#consent_blackbar[role=dialog][aria-label="consent banner"] #trustarc-banner-overlay[onclick]').all():
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
