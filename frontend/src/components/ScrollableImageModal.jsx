import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';

const ScrollableImageModal = ({ isOpen, images, onClose }) => {
  // Handle ESC key
  useEffect(() => {
    const handleEsc = (e) => {
      if (e.key === 'Escape') onClose();
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
    }

    return () => document.removeEventListener('keydown', handleEsc);
  }, [isOpen, onClose]);

  // Handle body scroll lock
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }

    return () => {
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  // Don't render if not open or no images
  if (!isOpen || !images || images.length === 0) {
    return null;
  }

  // Normalize images (handle base64 prefix)
  const normalizedImages = images.map(img =>
    typeof img === 'string' && !img.startsWith('data:')
      ? `data:image/jpeg;base64,${img}`
      : img
  );

  console.log('Modal rendering with', normalizedImages.length, 'images');

  const modalJSX = (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.95)',
        zIndex: 999999,
        overflowY: 'auto',
        overflowX: 'hidden'
      }}
    >
      {/* Fixed Close Button */}
      <button
        onClick={(e) => {
          e.stopPropagation();
          onClose();
        }}
        style={{
          position: 'fixed',
          top: 20,
          right: 20,
          width: 50,
          height: 50,
          borderRadius: '50%',
          border: 'none',
          backgroundColor: 'white',
          fontSize: 24,
          cursor: 'pointer',
          zIndex: 9999999,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center'
        }}
      >
        ✕
      </button>

      {/* Fixed Page Count */}
      <div
        style={{
          position: 'fixed',
          top: 20,
          left: 20,
          backgroundColor: 'rgba(0,0,0,0.8)',
          color: 'white',
          padding: '10px 20px',
          borderRadius: 5,
          fontSize: 16,
          zIndex: 9999999
        }}
      >
        Total: {normalizedImages.length} page(s)
      </div>

      {/* Scrollable Images Container */}
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: '80px 20px 40px 20px',
          gap: 40
        }}
      >
        {normalizedImages.map((imgSrc, idx) => (
          <div key={idx} style={{ textAlign: 'center' }}>
            <div style={{
              color: 'white',
              marginBottom: 15,
              fontSize: 18,
              fontWeight: 600
            }}>
              — Page {idx + 1} of {normalizedImages.length} —
            </div>
            <img
              src={imgSrc}
              alt={`Page ${idx + 1}`}
              style={{
                maxWidth: '90vw',
                height: 'auto',
                border: '4px solid white',
                borderRadius: 8,
                boxShadow: '0 0 40px rgba(255,255,255,0.15)'
              }}
              onError={(e) => {
                console.error('Image failed to load:', idx);
                e.target.style.display = 'none';
              }}
            />
          </div>
        ))}

        {/* Scroll indicator at bottom */}
        <div style={{ color: 'rgba(255,255,255,0.5)', padding: 20 }}>
          — End of Document —
        </div>
      </div>
    </div>
  );

  // Use portal to render at document.body level
  return createPortal(modalJSX, document.body);
};

export default ScrollableImageModal;
