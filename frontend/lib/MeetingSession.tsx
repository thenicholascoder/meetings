'use client';

import { useLocalParticipant } from '@livekit/components-react';
import React from 'react';
import type { MeetingRole } from '@/lib/meetingApi';

export type MeetingSession = {
  roomName: string;
  hostKey: string | null;
  participantKey: string | null;
  role: MeetingRole;
  roleEpoch?: number;
  setRole?: (role: MeetingRole) => void;
};

const MeetingSessionContext = React.createContext<MeetingSession | null>(null);

export function MeetingSessionProvider(props: {
  value: MeetingSession;
  setRole: (role: MeetingRole) => void;
  children: React.ReactNode;
}) {
  const provided = React.useMemo(
    () => ({ ...props.value, setRole: props.setRole }),
    [props.setRole, props.value],
  );
  return <MeetingSessionContext.Provider value={provided}>{props.children}</MeetingSessionContext.Provider>;
}

export function useMeetingSession() {
  return React.useContext(MeetingSessionContext);
}

export function roleFromAttributes(
  attributes: Record<string, string> | undefined,
  fallback: MeetingRole,
): MeetingRole {
  const role = attributes?.role;
  if (role === 'host' || role === 'cohost' || role === 'participant') {
    return role;
  }
  return fallback;
}

/**
 * The join token keeps role=participant until LiveKit applies the promotion.
 * Trust a co-host or host session over that stale attribute, and follow a
 * later attribute change when the host takes the title away.
 */
export function useMeetingRole(): MeetingRole {
  const session = useMeetingSession();
  const { localParticipant } = useLocalParticipant();
  const attributeRole = localParticipant.attributes?.role;
  const sessionRole = session?.role ?? 'participant';
  const epoch = session?.roleEpoch ?? 0;
  const sawPromotedAttribute = React.useRef(attributeRole === 'host' || attributeRole === 'cohost');
  const [followed, setFollowed] = React.useState<MeetingRole | null>(null);

  React.useEffect(() => {
    setFollowed(null);
  }, [epoch]);

  React.useEffect(() => {
    if (attributeRole === 'host' || attributeRole === 'cohost') {
      sawPromotedAttribute.current = true;
      setFollowed(attributeRole);
      return;
    }
    if (attributeRole === 'participant' && sawPromotedAttribute.current) {
      setFollowed('participant');
    }
  }, [attributeRole]);

  if (followed) {
    return followed;
  }
  if (sessionRole === 'host' || sessionRole === 'cohost') {
    return sessionRole;
  }
  return roleFromAttributes(localParticipant.attributes, sessionRole);
}
