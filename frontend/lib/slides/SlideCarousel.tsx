'use client';

import type { TrackReferenceOrPlaceholder } from '@livekit/components-core';
import { getScrollBarWidth } from '@livekit/components-core';
import { ParticipantTile, TrackLoop, useVisualStableUpdate } from '@livekit/components-react';
import * as React from 'react';
import { SlideStage } from '@/lib/slides/SlideStage';
import styles from '@/styles/Slides.module.css';

const MIN_HEIGHT = 130;
const MIN_WIDTH = 140;
const MIN_VISIBLE_TILES = 1;
const ASPECT_RATIO = 16 / 10;
const ASPECT_RATIO_INVERT = (1 - ASPECT_RATIO) * -1;

/** Same breakpoint as LiveKit's focus layout, which moves the strip under the stage. */
const NARROW_LAYOUT = '(max-width: 600px)';

/**
 * The side column used while a person is expanded during a slide share.
 * The deck sits in the first slot, the same place a minimized screen share occupies.
 */
export function SlideCarousel({
  tracks,
  capture = false,
}: {
  tracks: TrackReferenceOrPlaceholder[];
  capture?: boolean;
}) {
  const [asideEl, setAsideEl] = React.useState<HTMLElement | null>(null);
  const [prevTiles, setPrevTiles] = React.useState(0);
  const { width, height } = useElementSize(asideEl);
  const narrow = useNarrowLayout();
  // Follow the focus-layout breakpoint. Measuring the strip itself flips the
  // orientation late, and the old count then stays at 1 so the column scrolls.
  const carouselOrientation = narrow ? 'horizontal' : 'vertical';

  const hasSize = width > 0 && height > 0;
  const sizeMatchesOrientation =
    hasSize && (carouselOrientation === 'horizontal' ? width > height : height >= width);
  const tileSpan =
    carouselOrientation === 'vertical'
      ? Math.max(width * ASPECT_RATIO_INVERT, MIN_HEIGHT)
      : Math.max(height * ASPECT_RATIO, MIN_WIDTH);
  const scrollBarWidth = getScrollBarWidth();
  const tilesThatFit = sizeMatchesOrientation
    ? carouselOrientation === 'vertical'
      ? Math.max((height - scrollBarWidth) / tileSpan, MIN_VISIBLE_TILES)
      : Math.max((width - scrollBarWidth) / tileSpan, MIN_VISIBLE_TILES)
    : prevTiles;

  let maxVisibleTiles = prevTiles > 0 ? Math.round(prevTiles) : 0;
  if (sizeMatchesOrientation) {
    maxVisibleTiles = Math.round(tilesThatFit);
    if (Math.abs(tilesThatFit - prevTiles) < 0.5) {
      maxVisibleTiles = Math.round(prevTiles);
    } else if (prevTiles !== tilesThatFit) {
      setPrevTiles(tilesThatFit);
    }
  }

  const sortedTiles = useVisualStableUpdate(tracks, Math.max(maxVisibleTiles - 1, MIN_VISIBLE_TILES));
  const tileCount = 1 + sortedTiles.length;

  React.useLayoutEffect(() => {
    if (!asideEl) {
      return;
    }
    asideEl.dataset.lkOrientation = carouselOrientation;
    asideEl.scrollTop = 0;
    asideEl.scrollLeft = 0;
  }, [asideEl, carouselOrientation]);

  React.useLayoutEffect(() => {
    if (!asideEl) {
      return;
    }
    if (maxVisibleTiles > 0) {
      asideEl.style.setProperty('--lk-max-visible-tiles', maxVisibleTiles.toString());
    }
    const fits = maxVisibleTiles > 0 && tileCount <= maxVisibleTiles;
    if (carouselOrientation === 'vertical') {
      asideEl.style.overflowX = 'hidden';
      asideEl.style.overflowY = fits ? 'hidden' : 'auto';
    } else {
      asideEl.style.overflowY = 'hidden';
      asideEl.style.overflowX = fits ? 'hidden' : 'auto';
    }
  }, [asideEl, maxVisibleTiles, carouselOrientation, tileCount]);

  return (
    <aside className={`lk-carousel ${styles.carousel}`} data-lk-orientation={carouselOrientation} ref={setAsideEl}>
      <SlideStage layout="tile" capture={capture} />
      <TrackLoop tracks={sortedTiles}>
        <ParticipantTile />
      </TrackLoop>
    </aside>
  );
}

function useNarrowLayout() {
  const [matches, setMatches] = React.useState(false);

  React.useLayoutEffect(() => {
    const media = window.matchMedia(NARROW_LAYOUT);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  return matches;
}

function useElementSize(element: HTMLElement | null) {
  const [size, setSize] = React.useState({ width: 0, height: 0 });

  React.useLayoutEffect(() => {
    if (!element) {
      return;
    }
    const measure = () => {
      const rect = element.getBoundingClientRect();
      const width = Math.round(rect.width);
      const height = Math.round(rect.height);
      setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);

  return size;
}
