// Counts shown in headers and tab badges: numbers get thousands separators, anything else passes through.
export default function formatCount(count) {
    return typeof count === 'number' ? count.toLocaleString() : count;
}
