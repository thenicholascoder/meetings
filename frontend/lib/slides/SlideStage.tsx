'use client';

import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import React from 'react';
import { useLayoutContext, useParticipants } from '@livekit/components-react';
import { SlidesIcon } from '@/lib/slides/ShareSlidesButton';
import { useSlides } from '@/lib/slides/SlidesContext';
import styles from '@/styles/Slides.module.css';

let cachedPdf: { bytes: Uint8Array; pdf: PDFDocumentProxy } | null = null;

/**
 * The recorder must paint before it parses the deck. Opening the PDF on the
 * first frame blocks Chrome, the capture pipeline stays paused, and the MP4
 * only contains the short stretch after the page finally draws.
 */
const CAPTURE_PDF_DELAY_MS = 1500;

function isIgnoredRenderError(error: unknown) {
  if (!(error instanceof Error)) {
    return false;
  }
  return (
    error.name === 'RenderingCancelledException' ||
    error.message.includes('multiple render() operations') ||
    error.message.includes('Rendering cancelled')
  );
}

export function SlideStage({
  layout = 'stage',
  capture = false,
}: {
  layout?: 'stage' | 'tile';
  /** Egress captures this view. Draw at 1x so Chrome can keep up for the whole meeting. */
  capture?: boolean;
}) {
  const {
    deck,
    pdfBytes,
    fileStatus,
    fileError,
    isOwner,
    showSpeakerNotes,
    speakerNotes,
    notesLoaded,
    setPage,
  } = useSlides();
  const participants = useParticipants();
  const layoutContext = useLayoutContext();
  const frameRef = React.useRef<HTMLDivElement>(null);
  const canvasRef = React.useRef<HTMLCanvasElement>(null);
  const renderGeneration = React.useRef(0);
  const renderTaskRef = React.useRef<RenderTask | null>(null);
  const [pdf, setPdf] = React.useState<PDFDocumentProxy | null>(() =>
    cachedPdf && cachedPdf.bytes === pdfBytes ? cachedPdf.pdf : null,
  );
  const [renderError, setRenderError] = React.useState<string | null>(null);
  // The canvas is white until the first page is copied onto it. Stay on the
  // loading message until that blit, so the empty canvas never flashes.
  const [pageReady, setPageReady] = React.useState(false);
  const [allowPdf, setAllowPdf] = React.useState(!capture);

  React.useEffect(() => {
    if (!capture) {
      return;
    }
    const timer = window.setTimeout(() => setAllowPdf(true), CAPTURE_PDF_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [capture]);

  React.useEffect(() => {
    setPageReady(false);
    if (capture && !allowPdf) {
      return;
    }
    if (!pdfBytes) {
      setPdf(null);
      setRenderError(null);
      if (cachedPdf) {
        void cachedPdf.pdf.destroy();
        cachedPdf = null;
      }
      return;
    }
    if (cachedPdf?.bytes === pdfBytes) {
      setPdf(cachedPdf.pdf);
      setRenderError(null);
      return;
    }
    let cancelled = false;
    let document: PDFDocumentProxy | null = null;
    void (async () => {
      try {
        const pdfjs = await import('pdfjs-dist');
        pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';
        document = await pdfjs
          .getDocument({
            data: pdfBytes.slice(0),
            standardFontDataUrl: '/pdfjs/standard_fonts/',
            cMapUrl: '/pdfjs/cmaps/',
            cMapPacked: true,
          })
          .promise;
        if (cancelled) {
          await document.destroy();
          return;
        }
        if (cachedPdf && cachedPdf.bytes !== pdfBytes) {
          void cachedPdf.pdf.destroy();
        }
        cachedPdf = { bytes: pdfBytes, pdf: document };
        setPdf(document);
        setRenderError(null);
      } catch (error) {
        if (!cancelled) {
          setPdf(null);
          setRenderError(error instanceof Error ? error.message : 'Could not open this PDF');
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [allowPdf, capture, pdfBytes]);

  React.useEffect(() => {
    const frame = frameRef.current;
    const canvas = canvasRef.current;
    if (!pdf || !deck || !frame || !canvas) {
      return;
    }
    const pageNumber = Math.min(deck.page, pdf.numPages);

    const draw = () => {
      const generation = ++renderGeneration.current;
      const previous = renderTaskRef.current;
      if (previous) {
        previous.cancel();
        renderTaskRef.current = null;
      }

      void (async () => {
        try {
          const width = frame.clientWidth;
          const height = frame.clientHeight;
          if (width < 2 || height < 2 || generation !== renderGeneration.current) {
            return;
          }
          const page = await pdf.getPage(pageNumber);
          if (generation !== renderGeneration.current) {
            return;
          }
          const nextWidth = frame.clientWidth;
          const nextHeight = frame.clientHeight;
          if (nextWidth < 2 || nextHeight < 2) {
            return;
          }
          const ratio = Math.min(window.devicePixelRatio || 1, capture ? 1 : 2);
          const base = page.getViewport({ scale: 1 });
          const scale = Math.min(nextWidth / base.width, nextHeight / base.height);
          const viewport = page.getViewport({ scale: scale * ratio });
          // A fresh canvas per draw so a cancelled page cannot collide with the next one.
          const buffer = document.createElement('canvas');
          const context = buffer.getContext('2d');
          if (!context || generation !== renderGeneration.current) {
            return;
          }
          buffer.width = Math.floor(viewport.width);
          buffer.height = Math.floor(viewport.height);
          const task = page.render({ canvasContext: context, viewport });
          renderTaskRef.current = task;
          try {
            await task.promise;
          } finally {
            if (renderTaskRef.current === task) {
              renderTaskRef.current = null;
            }
          }
          if (generation !== renderGeneration.current) {
            return;
          }
          canvas.width = buffer.width;
          canvas.height = buffer.height;
          canvas.style.width = `${Math.floor(viewport.width / ratio)}px`;
          canvas.style.height = `${Math.floor(viewport.height / ratio)}px`;
          const shown = canvas.getContext('2d');
          if (!shown || generation !== renderGeneration.current) {
            return;
          }
          shown.drawImage(buffer, 0, 0);
          setRenderError(null);
          setPageReady(true);
        } catch (error) {
          if (generation !== renderGeneration.current || isIgnoredRenderError(error)) {
            return;
          }
          setRenderError(error instanceof Error ? error.message : 'Could not draw this slide');
        }
      })();
    };

    draw();
    let resizeTimer = 0;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(resizeTimer);
      // A burst of resizes (the recorder joining, the recording border) was
      // cancelling every draw, so the host kept the error line while the
      // page buttons still worked.
      resizeTimer = window.setTimeout(draw, 200);
    });
    observer.observe(frame);
    return () => {
      window.clearTimeout(resizeTimer);
      renderGeneration.current += 1;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      observer.disconnect();
    };
  }, [capture, deck, pdf]);

  React.useEffect(() => {
    if (!isOwner || !deck) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName;
        if (tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable) {
          return;
        }
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        setPage(deck.page + 1);
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        setPage(deck.page - 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [deck, isOwner, setPage]);

  if (!deck) {
    return null;
  }

  const owner = participants.find((participant) => participant.identity === deck.ownerIdentity);
  const ownerLabel = owner
    ? owner.name !== ''
      ? owner.name
      : owner.identity
    : deck.ownerName || deck.ownerIdentity || 'Someone';
  const slidesReady = Boolean(pdf) && pageReady;
  const message =
    renderError ||
    fileError ||
    (!slidesReady && fileStatus !== 'error' ? 'Loading slides…' : null);

  const showSlides = () => {
    layoutContext.pin.dispatch?.({ msg: 'clear_pin' });
  };

  const notesOpen = layout === 'stage' && isOwner && showSpeakerNotes;
  const note = speakerNotes[deck.page - 1] ?? '';
  const noteText = !notesLoaded ? 'Loading notes…' : note.trim() ? note : 'No notes on this slide';

  return (
    <div
      className={
        layout === 'tile'
          ? `lk-participant-tile ${styles.tile}`
          : `${styles.stage} ${notesOpen ? styles.stageWithNotes : ''}`
      }
    >
      <div ref={frameRef} className={styles.frame}>
        {!slidesReady && message ? <p className={styles.status}>{message}</p> : null}
        <canvas
          ref={canvasRef}
          aria-label={`Slide ${deck.page} of ${deck.pageCount}`}
          style={{ display: slidesReady ? 'block' : 'none' }}
        />
      </div>
      {notesOpen ? (
        <div className={styles.speakerNotes} aria-live="polite">
          <p className={styles.notesLabel}>Speaker notes</p>
          <p className={note.trim() || !notesLoaded ? styles.notesBody : `${styles.notesBody} ${styles.notesEmpty}`}>
            {noteText}
          </p>
        </div>
      ) : null}
      <div className="lk-participant-metadata">
        <div className="lk-participant-metadata-item">
          <SlidesIcon style={{ marginRight: '0.25rem' }} />
          <span className="lk-participant-name">{ownerLabel}&apos;s slides</span>
        </div>
      </div>
      {layout === 'tile' ? (
        <button
          type="button"
          className="lk-button lk-focus-toggle-button"
          aria-label="Expand slides"
          onClick={showSlides}
        >
          <ExpandIcon />
        </button>
      ) : null}
      {layout === 'stage' && (
        <div className={styles.hud}>
          <div className={styles.remote}>
            {isOwner && (
              <button
                type="button"
                className={`lk-button ${styles.navButton}`}
                onClick={() => setPage(deck.page - 1)}
                disabled={deck.page <= 1}
              >
                Previous
              </button>
            )}
            <span aria-live="polite">
              Slide {deck.page} of {deck.pageCount}
            </span>
            {isOwner && (
              <button
                type="button"
                className={`lk-button ${styles.navButton}`}
                onClick={() => setPage(deck.page + 1)}
                disabled={deck.page >= deck.pageCount}
              >
                Next
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5">
        <path d="M10 1.75h4.25m0 0V6m0-4.25L9 7M6 14.25H1.75m0 0V10m0 4.25L7 9" />
      </g>
    </svg>
  );
}
