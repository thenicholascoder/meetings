import { Suspense } from 'react';
import { EgressRoom } from '@/lib/EgressRoom';

export default function EgressPage() {
  return (
    <Suspense>
      <EgressRoom />
    </Suspense>
  );
}
