import React from 'react';
import Avatar from './Avatar';
import { useAuth } from '../../hooks/useAuth';

/**
 * The signed-in user's avatar (Phase OMS-USER-AVATAR-CONSISTENCY).
 *
 * WHY THIS EXISTS ON TOP OF `Avatar`
 * ----------------------------------
 * `Avatar` is the presentational piece: hand it a photo and some initials and
 * it renders one or the other, falling back if the image 404s. It says nothing
 * about WHOSE avatar it is, so every call site wired `photo`, `initials` and
 * `color` out of `useAuth` by hand — and one of them did not.
 *
 * The sidebar footer drew its own `<span>{user.initials}</span>`, so a user
 * with a profile photo saw their face in the header and "DM" in the rail: the
 * same person, two identities, on the same screen. That is not a styling bug,
 * it is three lines of duplicated wiring that drifted, and deleting the
 * duplication is the only fix that stays fixed.
 *
 * So: `Avatar` for anyone (a leave applicant, a workflow actor), `UserAvatar`
 * for the person who is logged in. Nothing that represents the current user
 * should read `user.profile_photo` directly again.
 *
 * `color` is overridable ONLY because CSS cannot reach the fallback tile —
 * `Avatar` writes its background inline, so a caller wanting the sidebar's
 * gradient has to pass it here. Any CSS value works, gradients included.
 */
const UserAvatar = ({
  size = 40, radius = '50%', className = '', fontSize, color, style,
}) => {
  const { user } = useAuth();
  return (
    <Avatar
      photo={user?.profile_photo}
      initials={user?.initials || 'U'}
      // The grey is for the signed-out/loading moment: a role colour on a
      // placeholder claims a role the app does not know yet.
      color={color || (user?.initials ? user.color : '#999')}
      size={size}
      radius={radius}
      fontSize={fontSize}
      className={className}
      style={style}
    />
  );
};

export default UserAvatar;
