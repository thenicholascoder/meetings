'use client';

import React from 'react';
import { useSlides } from '@/lib/slides/SlidesContext';
import styles from '@/styles/Slides.module.css';

const PPTX_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation';

type SlideFileHandle = { getFile: () => Promise<File> };

type ShowOpenFilePicker = (options: {
  multiple?: boolean;
  excludeAcceptAllOption?: boolean;
  types?: Array<{ description?: string; accept: Record<string, string[]> }>;
}) => Promise<SlideFileHandle[]>;

function isSlideFile(file: File) {
  const lowerName = file.name.toLowerCase();
  return lowerName.endsWith('.pdf') || lowerName.endsWith('.pptx');
}

export function ShareSlidesButton({
  showText,
  locked,
}: {
  showText: boolean;
  locked: boolean;
}) {
  const {
    deck,
    isOwner,
    hasSpeakerNotes,
    showSpeakerNotes,
    setShowSpeakerNotes,
    uploading,
    shareFile,
    stopSharing,
  } = useSlides();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const groupRef = React.useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = React.useState(false);
  const sharing = isOwner && !!deck;
  const busy = uploading;
  const lockedByPresentation = locked && !sharing && !busy;
  const notesMenu = sharing && hasSpeakerNotes;
  let label = 'Share slides';
  if (uploading) {
    label = 'Uploading…';
  } else if (sharing) {
    label = 'Stop slides';
  }

  React.useEffect(() => {
    if (!notesMenu) {
      setMenuOpen(false);
    }
  }, [notesMenu]);

  React.useEffect(() => {
    if (!menuOpen) {
      return;
    }
    const onPointer = (event: MouseEvent) => {
      if (!groupRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false);
      }
    };
    window.addEventListener('mousedown', onPointer);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onPointer);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const onClick = async () => {
    if (busy || lockedByPresentation) {
      return;
    }
    if (sharing) {
      setMenuOpen(false);
      void stopSharing();
      return;
    }
    const picker = (window as unknown as { showOpenFilePicker?: ShowOpenFilePicker }).showOpenFilePicker;
    if (!picker) {
      inputRef.current?.click();
      return;
    }
    try {
      const [handle] = await picker({
        multiple: false,
        excludeAcceptAllOption: true,
        types: [
          {
            description: 'PDF or PowerPoint',
            accept: {
              'application/pdf': ['.pdf'],
              [PPTX_TYPE]: ['.pptx'],
            },
          },
        ],
      });
      const file = await handle.getFile();
      if (isSlideFile(file)) {
        void shareFile(file);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        return;
      }
      inputRef.current?.click();
    }
  };

  const onFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (file && isSlideFile(file)) {
      void shareFile(file);
    }
  };

  const shareButton = (
    <button
      type="button"
      className={`lk-button ${styles.shareSlides}`}
      data-lk-enabled={sharing ? 'true' : 'false'}
      aria-pressed={sharing}
      aria-busy={busy}
      aria-disabled={lockedByPresentation || undefined}
      aria-label={lockedByPresentation ? 'Share slides unavailable while someone is presenting' : label}
      title={lockedByPresentation ? 'Unavailable while someone is presenting' : undefined}
      disabled={busy || lockedByPresentation}
      style={lockedByPresentation ? { pointerEvents: 'none' } : undefined}
      onPointerDown={(event) => {
        if (lockedByPresentation) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onClick={onClick}
    >
      <SlidesIcon />
      {showText && label}
    </button>
  );

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.pptx"
        hidden
        onChange={onFile}
      />
      {notesMenu ? (
        <div ref={groupRef} className={`lk-button-group ${styles.shareGroup}`}>
          {shareButton}
          <div className={`lk-button-group-menu ${styles.shareMenu}`}>
            <button
              type="button"
              className={`lk-button lk-button-menu ${styles.chevronButton}`}
              data-lk-enabled="true"
              aria-label="Speaker notes"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              disabled={busy}
              onClick={() => setMenuOpen((open) => !open)}
            />
            {menuOpen ? (
              <div className={styles.notesMenu} role="menu">
                <button
                  type="button"
                  role="menuitemcheckbox"
                  className={styles.notesToggle}
                  aria-checked={showSpeakerNotes}
                  onClick={() => {
                    setShowSpeakerNotes(!showSpeakerNotes);
                    setMenuOpen(false);
                  }}
                >
                  {showSpeakerNotes ? 'Hide speaker notes' : 'Show speaker notes'}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : (
        shareButton
      )}
    </>
  );
}

export function SlidesIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <rect x="3" y="4" width="18" height="12" rx="1.5" stroke="currentColor" strokeWidth="2" />
      <path d="M8 20h8M12 16v4M7 8h6M7 11h10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
