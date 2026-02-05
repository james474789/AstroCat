export default function RatingStars({ rating }) {
    // Clamp rating between 0 and 5
    const filledStars = Math.min(Math.max(Math.round(rating || 0), 0), 5);

    return (
        <div className="rating-stars">
            {[...Array(5)].map((_, i) => (
                <span key={i} className={`star ${i < filledStars ? 'filled' : 'empty'}`}>
                    {i < filledStars ? '★' : '☆'}
                </span>
            ))}
        </div>
    );
}
