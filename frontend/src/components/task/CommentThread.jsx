import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AtSign, MessageSquare, Pencil, Reply, Send } from 'lucide-react';

import { taskService } from '../../services/taskService';
import { fmtDateTime } from './taskLabels';

/**
 * Task discussion (Phase T2.3): comments, one level of replies, mentions and
 * edited markers.
 *
 * MENTIONS ARE RESOLVED, NOT PARSED
 * ---------------------------------
 * Typing "@" opens a person search; picking somebody inserts their name into the
 * text AND records their id. The id is what the server notifies on — the body is
 * plain text, so two colleagues called Bikash are indistinguishable in it.
 * A name typed by hand without picking from the list notifies nobody, which is
 * the honest outcome rather than a guess.
 *
 * EDITING IS THE AUTHOR'S ALONE
 * -----------------------------
 * The pencil appears only on your own comments, and only while the task is live
 * — but that is the SERVER's rule (tasks.permissions.can_edit_comment); this
 * just avoids offering a button that would 403.
 */

const MentionPicker = ({ onPick, onClose }) => {
  const [search, setSearch] = useState('');
  const { data: people = [] } = useQuery({
    queryKey: ['tasks', 'employees', search],
    queryFn: () => taskService.searchEmployees(search),
    enabled: search.trim().length >= 2,
    staleTime: 60_000,
    // The picker is a convenience; an employee who cannot reach the directory
    // endpoint just types the name instead of seeing an error.
    retry: false,
  });

  return (
    <div className="task-mention-picker">
      <label className="lr-field">
        <span className="sr-only">Search people to mention</span>
        <input autoFocus value={search} placeholder="Search people…"
          aria-label="Search people to mention"
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }} />
      </label>
      {search.trim().length >= 2 && (
        <ul className="task-picker" aria-label="People to mention">
          {people.length === 0 && (
            <li className="task-sub">Nobody matches that.</li>
          )}
          {people.map((person) => (
            <li key={person.id}>
              <button type="button" onClick={() => onPick(person)}>
                <b>{person.full_name}</b>
                <span className="task-sub">
                  {[person.designation, person.department].filter(Boolean).join(' · ') || '—'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

/** The shared composer: used for a new comment, a reply, and an edit. */
const Composer = ({
  label, initialBody = '', submitLabel, busy, onSubmit, onCancel, allowMentions = true,
}) => {
  const [body, setBody] = useState(initialBody);
  const [mentions, setMentions] = useState([]);
  const [picking, setPicking] = useState(false);

  const pick = (person) => {
    setBody((current) => `${current}${current && !current.endsWith(' ') ? ' ' : ''}@${person.full_name} `);
    setMentions((current) => (current.some((m) => m.id === person.id)
      ? current : [...current, person]));
    setPicking(false);
  };

  return (
    <div className="task-composer">
      <label className="lr-field">
        <span className="sr-only">{label}</span>
        <textarea value={body} rows={3} placeholder={label} aria-label={label}
          onChange={(e) => setBody(e.target.value)} />
      </label>

      {mentions.length > 0 && (
        <p className="task-sub">
          Will notify: {mentions.map((m) => m.full_name).join(', ')}
        </p>
      )}
      {picking && (
        <MentionPicker onPick={pick} onClose={() => setPicking(false)} />
      )}

      <div className="task-form-actions">
        {allowMentions && (
          <button type="button" className="lr-btn lr-btn-ghost"
            onClick={() => setPicking(!picking)}>
            <AtSign size={14} /> Mention
          </button>
        )}
        {onCancel && (
          <button type="button" className="lr-btn lr-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
        )}
        <button type="button" className="lr-btn lr-btn-primary"
          disabled={busy || !body.trim()}
          onClick={() => {
            onSubmit(body.trim(), mentions.map((m) => m.id));
            setBody('');
            setMentions([]);
          }}>
          <Send size={14} /> {submitLabel}
        </button>
      </div>
    </div>
  );
};

const Body = ({ comment }) => (
  <>
    <div className="task-comment-head">
      <b>{comment.author_name}</b>
      <span className="task-sub">{fmtDateTime(comment.created_at)}</span>
      {comment.is_edited && (
        // Stated in words, not only as a subtle style: an edit that a reader can
        // miss is an edit that has been hidden from them.
        <span className="task-edited" title={`Edited ${fmtDateTime(comment.edited_at)}`}>
          edited
        </span>
      )}
    </div>
    <p className="task-comment-body">{comment.body}</p>
    {comment.mentions?.length > 0 && (
      <p className="task-sub">
        <AtSign size={11} aria-hidden="true" />{' '}
        {comment.mentions.map((m) => m.user_name).join(', ')}
      </p>
    )}
  </>
);

const Comment = ({ comment, currentUserId, canComment, busy, onReply, onEdit }) => {
  const [replying, setReplying] = useState(false);
  const [editing, setEditing] = useState(false);
  const mine = comment.author?.id === currentUserId;

  return (
    <li className={comment.parent ? 'task-comment is-reply' : 'task-comment'}>
      {editing ? (
        <Composer
          label="Edit your comment" initialBody={comment.body}
          submitLabel="Save" busy={busy} allowMentions={false}
          onCancel={() => setEditing(false)}
          onSubmit={(body) => { onEdit(comment.id, body); setEditing(false); }}
        />
      ) : (
        <>
          <Body comment={comment} />
          <div className="task-comment-actions">
            {canComment && !comment.parent && (
              <button type="button" className="task-link-btn"
                onClick={() => setReplying(!replying)}>
                <Reply size={12} /> Reply
              </button>
            )}
            {mine && canComment && (
              <button type="button" className="task-link-btn"
                onClick={() => setEditing(true)}>
                <Pencil size={12} /> Edit
              </button>
            )}
          </div>
        </>
      )}

      {comment.replies?.length > 0 && (
        <ul className="task-replies">
          {comment.replies.map((reply) => (
            <Comment key={reply.id} comment={reply} currentUserId={currentUserId}
              canComment={canComment} busy={busy} onReply={onReply} onEdit={onEdit} />
          ))}
        </ul>
      )}

      {replying && (
        <div className="task-replies">
          <Composer label={`Reply to ${comment.author_name}`}
            submitLabel="Post reply"
            busy={busy} onCancel={() => setReplying(false)}
            onSubmit={(body, mentionIds) => {
              onReply(comment.id, body, mentionIds);
              setReplying(false);
            }} />
        </div>
      )}
    </li>
  );
};

const CommentThread = ({
  comments = [], currentUserId, canComment, busy, onPost, onReply, onEdit,
}) => (
  <>
    {comments.length === 0 && <p className="task-sub">No comments yet.</p>}
    <ul className="task-comments">
      {comments.map((comment) => (
        <Comment key={comment.id} comment={comment} currentUserId={currentUserId}
          canComment={canComment} busy={busy} onReply={onReply} onEdit={onEdit} />
      ))}
    </ul>
    {canComment && (
      <Composer label="Add a comment…" submitLabel="Post" busy={busy}
        onSubmit={(body, mentionIds) => onPost(body, mentionIds)} />
    )}
    {!canComment && comments.length > 0 && (
      <p className="task-sub">
        <MessageSquare size={12} aria-hidden="true" /> This task is closed —
        the discussion is read-only.
      </p>
    )}
  </>
);

export default CommentThread;
