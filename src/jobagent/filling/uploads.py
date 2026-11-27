"""Evidence for one upload: dispatched bytes plus acknowledgement by that widget."""
import base64
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


def resume_parse_request(url):
    # iCIMS appends this flag to an action that can already contain it after
    # a previous parse. Identical flags are unambiguous; conflicts are not.
    """Require one or more identical resume-parser flags and reject conflicting values."""
    flags=parse_qs(urlsplit(url).query,keep_blank_values=True).get('uploadResume',[])
    return bool(flags) and all(value=='1' for value in flags)


class ResumeParseReceipt:
    """iCIMS' own resume-only POST can replace the document and clear FileList.

    A filename is insufficient: require previously byte-verified dispatch,
    the corresponding successful same-frame uploadResume POST response, a new
    same-origin candidate document, and that field's completed server UI.
    """
    def __init__(self, native):
        self.frame=native.owner_frame()
        if self.frame is None:raise ValueError('Upload input lost its frame')
        self.page=self.frame.page
        self.field_id=native.get_attribute('id') or ''
        if self.field_id!='PortalProfileFields.Resume_File' or not self.frame.evaluate(
                "() => typeof icimsAllowResumeAutosubmit !== 'undefined' && Boolean(icimsAllowResumeAutosubmit)"):
            raise ValueError('Not a declared iCIMS resume parser')
        self.origin=urlsplit(self.frame.url)
        if not self.origin.path.rstrip('/').endswith('/candidate'):
            raise ValueError('Not a candidate profile')
        self.original=self.frame.evaluate_handle('document')
        self.accepted_document=None
        self.request=None;self.response_ok=False;self.requests=0
        self.observed_parse_posts=[];self.failure_reason='no fresh parse receipt'
        self.page.on('request',self.requested)
        self.page.on('response',self.responded)

    def same_document_route(self, url):
        """Check that a response remains on the original candidate origin and path."""
        p=urlsplit(url)
        return (p.scheme,p.netloc,p.path)==(self.origin.scheme,self.origin.netloc,self.origin.path)

    def requested(self, request):
        """Observe only this frame's fresh resume-parser navigation POST."""
        try:
            if request.method=='POST' and 'uploadResume' in parse_qs(urlsplit(request.url).query,keep_blank_values=True):
                self.observed_parse_posts.append({'same_frame':request.frame==self.frame,
                    'same_route':self.same_document_route(request.url),'navigation':request.is_navigation_request()})
            if (request.frame==self.frame and request.is_navigation_request() and request.method=='POST'
                    and self.same_document_route(request.url) and resume_parse_request(request.url)):
                self.requests+=1
                if self.requests==1:self.request=request
                else:self.response_ok=False  # another upload invalidates the previous receipt
        except Exception:pass

    def responded(self, response):
        """Record a successful response chain only for the matched parser POST."""
        try:
            request=response.request
            while request is not None:
                if not self.same_document_route(request.url):return
                if request==self.request:
                    self.response_ok=self.requests==1 and 200<=response.status<300
                    return
                request=request.redirected_from
        except Exception:pass

    def verify(self, name, dispatched_bytes_verified):
        """Verify dispatched bytes, a fresh parser response and the current owned widget metadata."""
        if not dispatched_bytes_verified:self.failure_reason='dispatched source bytes not verified';return False
        if not self.response_ok or self.requests!=1:self.failure_reason='no unique successful parse POST';return False
        try:
            if not self.same_document_route(self.frame.url):self.failure_reason='current route differs';return False
            try:
                if self.frame.evaluate('old=>document===old',self.original):self.failure_reason='original document still open';return False
            except Exception:pass  # a replaced execution context is expected after the parse POST
            if self.accepted_document is not None:
                try:
                    if not self.frame.evaluate('accepted=>document===accepted',self.accepted_document):self.failure_reason='accepted document replaced again';return False
                except Exception:self.failure_reason='accepted document no longer available';return False
            accepted=self.frame.evaluate('''name=>{
                const get=id=>{const es=[...document.querySelectorAll('[id]')].filter(e=>e.id===id);return es.length===1?es[0]:null;};
                const base='PortalProfileFields.Resume',input=get(base+'_File'),stored=get(base),filename=get(base+'_FileName'),url=get(base+'_URL'),box=get(base+'_Content');
                const visible=e=>!!e&&e.getBoundingClientRect().width>2&&e.getBoundingClientRect().height>2&&getComputedStyle(e).visibility!=='hidden';
                if(document.readyState==='loading'||!input||!input.form||!box?.contains(input)||!visible(box)||input.files?.length||
                   !stored||!filename||!url||stored.form!==input.form||filename.form!==input.form||url.form!==input.form||
                   stored.value!=='false'||url.value||visible(get(base+'_Loading')))return false;
                let actual;try{actual=decodeURIComponent(filename.value)}catch{return false}
                if(actual!==name||!box.innerText.includes(name))return false;
                if([...document.querySelectorAll('[aria-invalid=true],[role=alert],.iCIMS_Error,.iCIMS_ErrorMessage')].some(visible))return false;
                return [...box.querySelectorAll('button,input[type=button]')].some(e=>visible(e)&&
                    /^replace resume$/i.test((e.innerText||e.value||'').trim()));
            }''',name)
            if accepted and self.accepted_document is None:self.accepted_document=self.frame.evaluate_handle('document')
            self.failure_reason='' if accepted else 'fresh document lacks completed owned resume metadata'
            return bool(accepted)
        except Exception as exc:self.failure_reason='receipt inspection '+type(exc).__name__;return False

    def close(self):
        """Remove request observers and dispose retained document handles."""
        self.page.remove_listener('request',self.requested)
        self.page.remove_listener('response',self.responded)
        for handle in (self.original,self.accepted_document):
            if handle:
                try:handle.dispose()
                except Exception:pass

    def owns(self, element):
        """Check that an upload control belongs to the accepted parser document."""
        if self.accepted_document is None:return False
        try:
            return bool(element.evaluate('(e,a)=>e.ownerDocument===a.doc&&e.type==="file"&&e.id===a.id',
                                         {'doc':self.accepted_document,'id':self.field_id}))
        except Exception:return False


class _AttachmentDownload:
    """Verify a refreshed attachment through its current, ordinary Download action."""

    def __init__(self, widget):
        self.frame=widget.owner_frame();self.route=self.frame.url;self.document=None;self.refused=False;self.failure_reason=''
        self.caption=widget.evaluate(r'''e=>{
            const owner=e.closest('.RCMFormField.attachmentField');
            const labels=owner?.querySelectorAll(':scope > label');
            return /^\d+:_attach$/.test(e.id)&&e.closest('.attachmentComponentInput')&&labels?.length===1
                ?labels[0].textContent.replace(/\s+/g,' ').trim():'';
        }''')
        if self.caption:self.document=self.frame.evaluate_handle('document')

    def current_widget(self):
        """Find the unique same-document attachment owner after an ordinary edit."""
        if (self.document is None or self.frame.url!=self.route or
                not self.frame.evaluate('old=>document===old',self.document)):return None
        handle=self.frame.evaluate_handle(r'''caption=>{
            const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
            const owners=[...document.querySelectorAll('.RCMFormField.attachmentField')].filter(e=>visible(e)&&
                e.querySelectorAll(':scope > label').length===1&&
                e.querySelector(':scope > label').textContent.replace(/\s+/g,' ').trim()===caption);
            if(owners.length!==1)return null;
            const owner=owners[0];
            if(owner.querySelector('input:not([type=hidden]):not([type=file]),select,textarea,[role=combobox]'))return null;
            const boxes=[...owner.querySelectorAll('.attachmentBtn,.successBtn')].filter(e=>visible(e)&&/^\d+:_attach$/.test(e.id));
            return boxes.length===1?boxes[0]:null;
        }''',self.caption)
        widget=handle.as_element()
        if widget is None:handle.dispose()
        return widget

    def owns(self, element, name):
        """Bind a current control to the exact owner of this download action."""
        action=self._action(name)
        if action is None:return False
        try:return bool(element.evaluate('(e,a)=>{const box=a.closest(".successBtn");return !!box&&(e===box||box.contains(e))}',action))
        finally:action.dispose()

    def _action(self, name):
        if (self.refused or self.document is None or self.frame.url!=self.route or
                not self.frame.evaluate('old=>document===old',self.document)):return None
        handle=self.frame.evaluate_handle(r'''args=>{
            const visible=e=>!!e&&e.getBoundingClientRect().width>2&&e.getBoundingClientRect().height>2&&getComputedStyle(e).visibility!=='hidden';
            const owners=[...document.querySelectorAll('.RCMFormField.attachmentField')].filter(e=>visible(e)&&
                e.querySelectorAll(':scope > label').length===1&&
                e.querySelector(':scope > label').textContent.replace(/\s+/g,' ').trim()===args.caption);
            if(owners.length!==1)return null;
            const owner=owners[0];
            if(owner.querySelector('input:not([type=file]):not([type=hidden]),select,textarea,[role=combobox]'))return null;
            const widgets=[...owner.querySelectorAll('.successBtn[id$=":_attach"]')].filter(visible);
            if(widgets.length!==1)return null;
            const box=widgets[0],get=suffix=>box.querySelector('[id="'+CSS.escape(box.id.replace(/_attach$/,suffix))+'"]');
            const action=get('_attachDownloadLabel'),filename=get('_attachDownloadLabelLink');
            if(!/^\d+:_attach$/.test(box.id)||!visible(action)||action.getAttribute('role')!=='button'||
               box.querySelectorAll('[id$="_attachDownloadLabelLink"]').length!==1||
               filename?.textContent.trim()!==args.name||box.matches('[aria-invalid=true],[aria-busy=true]'))return null;
            if([...owner.querySelectorAll('[aria-invalid=true],[aria-busy=true],[role=alert],[role=progressbar],progress')].some(e=>visible(e)&&
                !(e.id===box.id.replace(/_attach$/,'_attachSuccess')&&e.getAttribute('aria-invalid')!=='true'&&
                  e.getAttribute('aria-busy')!=='true'&&(e.innerText||'').trim()==='File is uploaded successfully')))return null;
            return action;
        }''',{'caption':self.caption,'name':name})
        action=handle.as_element()
        if action is None:handle.dispose()
        return action

    def verify(self, name, expected, size):
        """Hash the bounded same-origin URL produced by this exact visible action.

        Args:
            name: Filename expected in the current attachment widget.
            expected: SHA-256 digest of the supplied application document.
            size: Exact byte length of that document.
        """
        action=None;download=None;response=None;current=None
        try:
            from jobagent.tools import challenge
            page=self.frame.page
            policy=getattr(page.context,'jobagent_policy',None)
            if policy:policy(page)
            if challenge.detect(page) is not None:return False
            action=self._action(name)
            if action is None:
                if not self.failure_reason:self.failure_reason='current owned download action unavailable'
                return False
            with page.expect_download(timeout=5000) as event:action.click(timeout=5000)
            download=event.value
            target,origin=urlsplit(download.url),urlsplit(self.route)
            if (target.scheme not in ('https','http') or target.netloc!=origin.netloc or target.scheme!=origin.scheme or
                    target.username or target.password or target.path!='/attachmentdownload' or
                    download.suggested_filename!=name):
                self.refused=True;self.failure_reason='download origin, endpoint or filename did not match';return False
            # Browser download bodies are not exposed by Response.body(). Read
            # the URL that this action produced with the same browser cookies,
            # a fixed timeout and no redirects; never guess a file endpoint.
            response=page.request.get(download.url,timeout=5000,max_redirects=0)
            if not response.ok:self.refused=True;self.failure_reason=f'attachment download HTTP {response.status}';return False
            length=response.headers.get('content-length')
            if length and int(length)>5_000_000:self.refused=True;self.failure_reason='attachment download exceeds size limit';return False
            content=response.body()
            current=self._action(name)
            matched=len(content)==size and hashlib.sha256(content).hexdigest()==expected
            if not matched:self.refused=True;self.failure_reason='current server bytes differ from the supplied document'
            valid=bool(matched and current is not None and current.evaluate('(e,old)=>e===old',action))
            if valid:self.failure_reason=''
            return valid
        except Exception as exc:
            self.refused=True;self.failure_reason='attachment download '+type(exc).__name__
            return False
        finally:
            if response is not None:
                try:response.dispose()
                except Exception:pass
            if download is not None:
                try:download.cancel()
                except Exception:pass
            for handle in (action,current):
                if handle is not None:
                    try:handle.dispose()
                    except Exception:pass

    def close(self):
        """Release the original document identity retained for download validation."""
        if self.document is not None:
            try:self.document.dispose()
            except Exception:pass
        self.document=None


class UploadProof:
    def __init__(self, path, trigger=None, *, role_caption=''):
        """Bind a supplied document to its widget and explicit DOM section caption."""
        self.path=Path(path)
        content=self.path.read_bytes()
        self.expected=hashlib.sha256(content).hexdigest();self.size=len(content);self.name=self.path.name
        self.native=None;self.widget=None;self.token=None;self.form=None
        self.method='';self.replaced_count=0
        self.server_failures={}
        self.parse_receipt=None;self.dispatched_bytes_verified=False;self.parse_receipt_disabled_reason=''
        self.reused_existing=False
        self.role_caption=role_caption
        self.attachment_download=None
        if trigger:
            self.widget=self.widget_for(trigger)
            self.form=trigger.evaluate_handle('e=>e.form||e.closest("form")').as_element()
        self.before=self.widget.inner_text() if self.widget else ''
        if self.widget:
            try:self.attachment_download=_AttachmentDownload(self.widget)
            except Exception:pass

    @staticmethod
    def widget_for(element):
        handle=element.evaluate_handle(r'''e=>{
            // SuccessFactors keeps the acknowledgement and progress next to
            // attachActions, not inside it. Bind only this exact component.
            const match=(e.id||'').match(/^(\d+):_attach(?:Icon)?$/);
            const attachment=e.closest('.attachmentBtn,.successBtn');
            if(match && attachment?.id===match[1]+':_attach' &&
               attachment.querySelectorAll('[id$=":_attachIcon"]').length<=1 &&
               !attachment.querySelector('input:not([type=hidden]):not([type=file]),select,textarea,[role=combobox]'))return attachment;
            const field=e.parentElement?.closest('fieldset,.ant-form-item,[data-field-path],[data-field-entry-id],[data-automation-id^=formField-],.file-upload,.document-upload,.form-field,.form-group');
            if(field && field.querySelectorAll('input[type=file]').length<=1 &&
               ![...field.querySelectorAll('input:not([type=file]):not([type=hidden]),select,textarea,[role=combobox]')].some(c=>c!==e))return field;
            let candidate=null;
            for(let a=e.parentElement;a && !a.matches('form,body,html');a=a.parentElement){
                const controls=[...a.querySelectorAll('input,select,textarea,[role=combobox],button,[role=button]')];
                if(controls.some(c=>c!==e && !e.contains(c)))break;
                candidate=a;
                if(a.matches('fieldset,[data-field-path],[data-field-entry-id],[class*=upload i],.form-group,.form-field'))break;
            }
            return candidate;
        }''')
        return handle.as_element()

    def prepare(self):
        """Prepare the owned primary document before staging new bytes.

        Shared lists require an exact filename. One prior file in an explicit
        resume/cover-letter slot may have a different name; ambiguous lists and
        recognizable documents of another role are left untouched.
        """
        if not self.widget:return
        if self.attachment_download and self.attachment_download.caption:
            if self._prepare_existing_attachment():return True
        for _ in range(9):
            if not self.widget.evaluate('box=>box.isConnected'):
                raise ValueError('Upload widget changed while preparing its attachments')
            all_cards=self.widget.query_selector_all('[data-automation-id=file-upload-item]')
            cards=[]
            for card in all_cards:
                caption=card.query_selector('[data-automation-id=file-upload-item-name]')
                if caption and caption.inner_text().strip()==self.name:cards.append(card)
            if not cards and all_cards and not self.replaced_count:
                # One old primary document can have a different filename.
                # A shared/ambiguous document list is not safe to empty.
                import re
                labels=self.widget.query_selector_all('label,legend')
                caption=labels[0].inner_text().strip(' *:') if len(labels)==1 else ''
                pattern=r'(?i)(?:upload |attach )?(?:a |your )?(resume(?:\s*/\s*cv)?|cv|curriculum vitae|cover\s*letter)'
                role=re.fullmatch(pattern,caption) or re.fullmatch(pattern,self.role_caption.strip(' *:'))
                old=all_cards[0].query_selector('[data-automation-id=file-upload-item-name]') if len(all_cards)==1 else None
                old_name=old.inner_text().strip() if old else ''
                cover=bool(role and re.search(r'(?i)cover',role[1]))
                other_role=bool(re.search(r'(?i)resume|\bcv\b',old_name)) if cover else bool(re.search(r'(?i)cover.?letter|\bcover\b',old_name))
                if not role or not old_name or other_role:
                    raise ValueError('Existing attachments are not one unambiguous primary document; not replacing them')
                cards=all_cards
            if not cards:
                self.before=self.widget.inner_text()
                return
            if len(cards)>8 or self.replaced_count>=8:
                raise ValueError('Too many same-named attachments to replace safely')
            actions=[]
            for card in cards:
                old_name=card.query_selector('[data-automation-id=file-upload-item-name]').inner_text().strip()
                buttons=card.query_selector_all('button[data-automation-id=delete-file]')
                if len(buttons)!=1 or buttons[0].get_attribute('type')!='button' or \
                        buttons[0].get_attribute('aria-label')!='Delete '+old_name or \
                        not buttons[0].is_visible() or buttons[0].is_disabled():
                    raise ValueError('Existing attachment has no unambiguous non-submitting delete control')
                actions.append((card,buttons[0],card.owner_frame()))
            card,button,frame=actions[0]
            if frame is None:raise ValueError('Existing attachment lost its frame')
            button.click(timeout=5000)
            frame.wait_for_function('(card)=>!card.isConnected',arg=card,timeout=8000)
            self.replaced_count+=1
        raise ValueError('Same-named attachments did not settle after replacement')

    def _prepare_existing_attachment(self):
        """Reuse exact server bytes, or replace only the current owned attachment."""
        download=self.attachment_download
        widget=download.current_widget()
        if widget is None:raise ValueError('The attachment owner is no longer unique in this document')
        if not widget.evaluate("e=>e.matches('.successBtn')"):
            widget.dispose();return
        names=widget.query_selector_all('[id$=":_attachDownloadLabelLink"]')
        if len(names)!=1:
            for name in names:name.dispose()
            widget.dispose();raise ValueError('Completed attachment has no unique filename')
        current=names[0]
        old_name=current.inner_text().strip();current.dispose()
        page=download.frame.page
        if policy:=getattr(page.context,'jobagent_policy',None):policy(page)
        from jobagent.tools import challenge
        if challenge.detect(page) is not None:
            widget.dispose();raise ValueError('Human verification blocks attachment replacement')
        if download.frame.locator('[role=dialog]').filter(visible=True).count():
            widget.dispose();raise ValueError('An existing dialog blocks attachment replacement')
        if old_name==self.name:
            if download.verify(self.name,self.expected,self.size):
                self.method='current owned server attachment download bytes';self.reused_existing=True;widget.dispose();return True
            if download.failure_reason!='current server bytes differ from the supplied document':
                widget.dispose();raise ValueError('Existing attachment bytes could not be verified; not deleting it')
        deletes=widget.query_selector_all('[id$=":_attachIcon"].removeAttachments[role=button]')
        delete=deletes[0] if len(deletes)==1 else None
        valid=bool(delete and delete.is_visible() and not delete.is_disabled() and delete.evaluate(r'''(e,args)=>{
            const box=e.closest('.successBtn'),text=n=>n.textContent.replace(/\s+/g,' ').trim();
            const labels=(e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>e.ownerDocument.getElementById(id)).filter(Boolean);
            const action='Delete document '+args.name+' Opens a dialog';
            return box && e.id===box.id.replace(/_attach$/,'_attachIcon') &&
                labels.some(n=>box.contains(n)&&text(n)===action) &&
                labels.every(n=>text(n)===args.caption||text(n)===action);
        }''',{'name':old_name,'caption':download.caption}))
        if not valid:
            for item in deletes:item.dispose()
            widget.dispose();raise ValueError('No unambiguous delete action belongs to this attachment')
        try:
            delete.click(timeout=5000)
            dialog=download.frame.locator('[role=dialog]').filter(visible=True)
            try:dialog.first.wait_for(state='visible',timeout=2000)
            except Exception:pass
            if dialog.count()!=1 or 'Are you sure you want to delete this file?' not in dialog.inner_text() or \
                    dialog.locator('input:not([type=hidden]),select,textarea').count():
                raise ValueError('The attachment delete confirmation is missing or ambiguous')
            import re
            ok=dialog.get_by_role('button',name=re.compile('^OK$',re.I))
            cancel=dialog.get_by_role('button',name=re.compile('^Cancel$',re.I))
            if ok.count()!=1 or cancel.count()!=1 or ok.is_disabled():
                raise ValueError('The attachment delete confirmation has unexpected controls')
            ok.click(timeout=5000)
            dialog.wait_for(state='hidden',timeout=8000)
            replacement=download.current_widget()
            if replacement is None:raise ValueError('The cleared attachment owner changed documents or became ambiguous')
            icon=replacement.query_selector('[id$=":_attachIcon"].addAttachments[role=button]')
            if icon is None or replacement.query_selector('[id$=":_attachDownloadLabelLink"]'):
                replacement.dispose();raise ValueError('The owned attachment did not clear to an upload control')
            icon.dispose()
            self.widget.dispose();self.widget=replacement
            if self.form:self.form.dispose()
            self.form=replacement.evaluate_handle('e=>e.form||e.closest("form")').as_element()
            download.close();self.attachment_download=_AttachmentDownload(replacement)
            self.replaced_count+=1;self.before=replacement.inner_text()
        finally:
            delete.dispose();widget.dispose()

    def bind(self, native):
        self.native=native.element_handle() if hasattr(native,'element_handle') else native
        try:self.parse_receipt=ResumeParseReceipt(self.native)
        except ValueError as exc:self.parse_receipt_disabled_reason=str(exc)
        self.portaled=self.native.get_attribute('data-jf-upload-portaled')=='1'
        if self.portaled and not self.widget:
            raise ValueError('A portaled upload requires its original attachment widget')
        if self.replaced_count and not self.native.evaluate('e=>e.isConnected'):
            raise ValueError('Upload input changed after replacing its old attachment; read the widget again')
        self.token=self.native.evaluate('''e=>{
            const w=e.ownerDocument.defaultView, token=crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
            w.__jobagentUploadProofs ||= new Map();
            const state={file:null,digestFile:null,digest:null};w.__jobagentUploadProofs.set(token,state);
            const capture=event=>{if(event.target===e && e.files?.length===1)state.file=e.files[0]};
            e.ownerDocument.addEventListener('input',capture,true);
            e.ownerDocument.addEventListener('change',capture,true);
            state.cleanup=()=>{e.ownerDocument.removeEventListener('input',capture,true);
                e.ownerDocument.removeEventListener('change',capture,true)};
            return token;
        }''')
        if not self.widget:
            self.widget=self.widget_for(self.native)
            self.before=self.widget.inner_text() if self.widget else ''
        self.initially_inside=bool(self.widget and self.widget.evaluate('(box,n)=>box.contains(n)',self.native))
        self.form_owned=bool(not self.portaled and self.form and self.native.evaluate('(n,form)=>form.isConnected && n.form===form && !!n.name && !n.disabled',self.form))
        if self.portaled:
            self.native.evaluate('''(e,args)=>{
                const state=e.ownerDocument.defaultView.__jobagentUploadProofs.get(args.token);
                state.widget=args.widget;state.busySeen=false;
                const visible=n=>!!(n.getBoundingClientRect().width&&n.getBoundingClientRect().height)&&getComputedStyle(n).visibility!=='hidden';
                const inspect=()=>{if(state.file && [...state.widget.querySelectorAll('[aria-busy=true],[role=progressbar],progress,[id$=":_attachLoading"]')].some(visible))state.busySeen=true;};
                const observer=new MutationObserver(inspect);observer.observe(state.widget,{subtree:true,attributes:true,childList:true,characterData:true});
                const changed=event=>{if(event.type==='drop' || event.target.closest?.('[id$=":_attachIcon"]'))state.invalidated=true;};
                state.widget.addEventListener('click',changed,true);state.widget.addEventListener('drop',changed,true);
                const cleanup=state.cleanup;state.cleanup=()=>{cleanup();observer.disconnect();state.widget.removeEventListener('click',changed,true);state.widget.removeEventListener('drop',changed,true)};
            }''',{'token':self.token,'widget':self.widget})

    def owns(self, element):
        if self.can_rebind(element):return True
        try:
            return bool(self.native and element.evaluate('(e,n)=>e===n',self.native) or
                        self.widget and element.evaluate('(e,box)=>box.isConnected && box.contains(e)',self.widget))
        except Exception:return False

    def can_rebind(self, element):
        """Require a parser receipt or actual current bytes from the exact owned download."""
        try:
            if self.attachment_download and self.attachment_download.owns(element,self.name) and \
                    self.attachment_download.verify(self.name,self.expected,self.size):return True
        except Exception:pass  # a navigation invalidates this ownership, never grants it
        return bool(self.parse_receipt and self.parse_receipt.verify(self.name,self.dispatched_bytes_verified)
                    and self.parse_receipt.owns(element))

    def diagnostic(self):
        """Return bounded upload-proof diagnostics without endpoint or authentication values."""
        receipt=self.parse_receipt
        return {'parse_receipt_enabled':receipt is not None,'disabled_reason':self.parse_receipt_disabled_reason,
                **({'attachment_download_failure':self.attachment_download.failure_reason} if self.attachment_download and self.attachment_download.caption else {}),
                'dispatched_bytes_verified':self.dispatched_bytes_verified,
                **({'parse_posts':receipt.requests,'response_ok':receipt.response_ok,
                    'observed_parse_posts':receipt.observed_parse_posts[:3],'failure_reason':receipt.failure_reason} if receipt else {})}

    def server_bytes_match(self):
        """Read this widget's current same-origin download, never infer bytes from its filename."""
        if not self.widget:return False
        try:
            urls=self.widget.evaluate('''(box,name)=>{
                const names=[name.toLowerCase(),...['download','view','open'].map(x=>x+' '+name.toLowerCase())];
                return [...new Set([...box.querySelectorAll('a[href]')].filter(a=>
                    names.includes((a.textContent||'').replace(/\\s+/g,' ').trim().toLowerCase()) || a.getAttribute('download')===name
                ).map(a=>{try{const u=new URL(a.getAttribute('href'),a.ownerDocument.location.href);
                    return ['https:','http:'].includes(u.protocol)&&u.origin===a.ownerDocument.location.origin&&
                        !u.username&&!u.password&&!u.href.includes('#')?u.href:null;}catch{return null}}).filter(Boolean))];
            }''',self.name)
            if len(urls)!=1:return False
            url=urls[0]
            if self.server_failures.get(url,0)>=3:return False
            frame=self.widget.owner_frame()
            if frame is None:return False
            response=frame.page.request.get(url,timeout=5000,max_redirects=5)
            try:
                if not response.ok:
                    self.server_failures[url]=self.server_failures.get(url,0)+1
                    return False
                length=response.headers.get('content-length')
                if length and int(length)>5_000_000:return False
                content=response.body()
            finally:response.dispose()
            self.server_failures[url]=0
            return len(content)==self.size and hashlib.sha256(content).hexdigest()==self.expected
        except Exception:
            if 'url' in locals():self.server_failures[url]=self.server_failures.get(url,0)+1
            return False

    def verify(self):
        """Verify dispatched bytes, or recheck the current owned server download."""
        if self._verify_dispatched():return True
        if self.attachment_download and self.attachment_download.verify(self.name,self.expected,self.size):
            self.method='current owned server attachment download bytes'
            return True
        return False

    def _verify_dispatched(self):
        if not self.native:return False
        if self.parse_receipt and self.parse_receipt.verify(self.name,self.dispatched_bytes_verified):
            self.method='byte-verified dispatch and fresh resume-parse POST receipt'
            return True
        try:
            if self.widget and self.widget.evaluate('''(box,args)=>{
                const visible=e=>!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height)&&getComputedStyle(e).visibility!=='hidden';
                const success=e=>e.getAttribute('role')==='alert' && e.getAttribute('aria-invalid')!=='true' &&
                    e.getAttribute('aria-busy')!=='true' && box.matches('.attachmentBtn,.successBtn') && e.id===box.id.replace(/_attach$/,'_attachSuccess') &&
                    (e.innerText||'').trim()==='File is uploaded successfully';
                return !box.isConnected || (args.inside&&args.native.isConnected&&!box.contains(args.native)) ||
                    [...box.querySelectorAll('input[type=file]')].some(e=>e!==args.native&&e.files?.length) ||
                    [...box.querySelectorAll('[aria-invalid=true],[role=alert],[aria-busy=true],[role=progressbar],progress,[class*=progress]')].some(e=>visible(e)&&!success(e));
            }''',{'native':self.native,'inside':getattr(self,'initially_inside',False)}):return False
            evidence=self.native.evaluate(r'''async (e,token)=>{
                const state=e.ownerDocument.defaultView.__jobagentUploadProofs?.get(token);
                if(state?.invalidated)return null;
                const retained=e.isConnected && e.files?.[0];
                const f=retained || state?.file;if(!f || f.size>5000000)return null;
                let digest=state?.digestFile===f ? state.digest : null;
                if(!digest){
                    const bytes=new Uint8Array(await f.arrayBuffer());
                    if(globalThis.crypto?.subtle){const sha=await crypto.subtle.digest('SHA-256',bytes);digest={sha256:[...new Uint8Array(sha)].map(n=>n.toString(16).padStart(2,'0')).join('')};}
                    else {let s='';for(let i=0;i<bytes.length;i+=8192)s+=String.fromCharCode(...bytes.subarray(i,i+8192));digest={bytes:btoa(s)};}
                    if(state){state.digestFile=f;state.digest=digest;}
                }
                const widget=state?.widget;
                const success=widget?.querySelector('[id="'+CSS.escape(widget.id.replace(/_attach$/,'_attachSuccess'))+'"]');
                const acknowledged=!!(state?.busySeen && widget?.isConnected && widget.matches('.attachmentBtn,.successBtn') &&
                    /^\d+:_attach$/.test(widget.id) && success &&
                    success.getBoundingClientRect().width && success.getBoundingClientRect().height &&
                    getComputedStyle(success).visibility!=='hidden' && (success.innerText||'').trim()==='File is uploaded successfully' &&
                    (widget.innerText||'').includes(f.name));
                return {...digest,name:f.name,retained:!!retained,acknowledged,invalid:e.isConnected &&
                    (e.getAttribute('aria-invalid')==='true' || (retained && e.validity && !e.validity.valid))};
            }''',self.token)
            if not evidence or evidence['invalid']:return False
            actual=evidence.get('sha256') or hashlib.sha256(base64.b64decode(evidence['bytes'])).hexdigest()
            if actual!=self.expected:return False
            self.dispatched_bytes_verified=True
            self.name=evidence['name']
            form_owned=bool(getattr(self,'form_owned',False) and self.native.evaluate('(n,form)=>form.isConnected && n.form===form && !!n.name && !n.disabled',self.form))
            if evidence['retained'] and (not self.widget or getattr(self,'initially_inside',False) or form_owned):
                self.dispatched_bytes_verified=True
                self.method='native bytes';return True
            if getattr(self,'portaled',False) and evidence.get('acknowledged'):
                self.method='dispatched bytes and fresh widget success';return True
            if evidence['name'] not in self.before and self.widget and self.widget.evaluate('''(box,name)=>{
                const visible=e=>!!(e.getBoundingClientRect().width&&e.getBoundingClientRect().height)&&getComputedStyle(e).visibility!=='hidden';
                const success=e=>e.getAttribute('role')==='alert' && e.getAttribute('aria-invalid')!=='true' &&
                    e.getAttribute('aria-busy')!=='true' && box.matches('.attachmentBtn,.successBtn') && e.id===box.id.replace(/_attach$/,'_attachSuccess') &&
                    (e.innerText||'').trim()==='File is uploaded successfully';
                return box.isConnected && (box.innerText||'').includes(name) &&
                    ![...box.querySelectorAll('[aria-invalid=true],[role=alert]')].some(e=>visible(e)&&!success(e));
            }''',evidence['name']):
                self.method='dispatched bytes and widget acknowledgement';return True
            if self.server_bytes_match():
                self.method='dispatched bytes and current server attachment bytes';return True
        except Exception:
            pass
        return False

    def close(self):
        if self.attachment_download:
            self.attachment_download.close();self.attachment_download=None
        if self.parse_receipt:
            self.parse_receipt.close();self.parse_receipt=None
        if self.native:
            try:self.native.evaluate('(e,t)=>{const m=e.ownerDocument.defaultView.__jobagentUploadProofs;m?.get(t)?.cleanup();m?.delete(t)}',self.token)
            except Exception:pass
        for handle in (self.native,self.widget,self.form):
            if handle:
                try:handle.dispose()
                except Exception:pass
        self.native=None;self.widget=None;self.form=None
