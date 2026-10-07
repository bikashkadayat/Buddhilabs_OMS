import React from 'react';
import { ArrowRight, CheckCircle2, Copy } from 'lucide-react';

import Modal from '../common/Modal';
import ShareMenu from './ShareMenu';
import { useCopyLink } from './useCopyLink';

/**
 * "Created successfully — here is the link" (Phase TASK-DEEP-LINK-SHARING).
 *
 * THE MOMENT THE REFERENCE IS WORTH SOMETHING
 * -------------------------------------------
 * Creating a record used to drop straight onto its detail page. Nothing was
 * wrong with that except the one thing people actually do next: tell somebody.
 * They would copy the number out of the header, paste it into WhatsApp, and the
 * recipient would search for it — which is the friction this phase exists to
 * remove. The reference is never more useful than in the second after it is
 * issued, so that is when the link is offered.
 *
 * REUSABLE: it takes a kind, a reference, a title and a url. A memo or a
 * circular showing this dialog needs no change here.
 */
const RecordCreatedDialog = ({
  kind = 'Task', reference, title, url, onOpen, onClose, onCopied, onFailed,
}) => {
  const [copy] = useCopyLink();

  const doCopy = async () => {
    const ok = await copy(url);
    if (ok) onCopied?.();
    else onFailed?.(url);
  };

  return (
    <Modal title={`${kind} created successfully`} onClose={onClose} width="460px"
      footer={(
        <>
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onClose}>
            Close
          </button>
          <button type="button" className="lr-btn lr-btn-primary" onClick={onOpen}>
            Open {kind.toLowerCase()} <ArrowRight size={14} />
          </button>
        </>
      )}>
      <div className="share-created">
        <p className="share-created-tick">
          <CheckCircle2 size={18} aria-hidden="true" />
          <span>{title}</span>
        </p>

        {/* The reference, big and selectable. Somebody reading it down a phone
            line needs it legible; somebody quoting it in a ticket needs to be
            able to select it without selecting the sentence around it. */}
        <p className="share-created-label">Reference</p>
        <p className="share-created-ref">{reference}</p>

        <p className="share-created-label">Link</p>
        <p className="share-created-url" title={url}>{url}</p>

        <div className="share-created-actions">
          <button type="button" className="lr-btn" onClick={doCopy}>
            <Copy size={14} /> Copy link
          </button>
          <ShareMenu kind={kind} title={title} reference={reference} url={url}
            onCopied={onCopied} onFailed={onFailed} />
        </div>
      </div>
    </Modal>
  );
};

export default RecordCreatedDialog;
