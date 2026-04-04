export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`animate-pulse bg-surface-hover/60 rounded-xl ${className}`} />
  );
}
