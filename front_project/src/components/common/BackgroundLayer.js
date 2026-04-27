import background from '../../assets/icons/background.jpg';

export default function BackgroundLayer() {
  return (
    <div
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        backgroundImage: `url(${background})`,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'center',
        backgroundSize: 'cover',
        zIndex: 0,
        pointerEvents: 'none',
      }}
    />
  );
}
