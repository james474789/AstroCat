import React, { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { LogIn, Loader2 } from 'lucide-react';
import './Login.css';

const Login = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const { login, error: authError } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();

    const from = location.state?.from?.pathname || '/search';

    const handleSubmit = async (e) => {
        e.preventDefault();
        setIsSubmitting(true);
        try {
            await login(email, password);
            navigate(from, { replace: true });
        } catch (err) {
            // Error is handled by AuthContext
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="login-container">
            <div className="login-card">
                <div className="login-header">
                    <div className="logo-icon">🌌</div>
                    <h1>{import.meta.env.VITE_LOGO_TITLE || 'AstroCat'}</h1>
                    <p>Astronomical Image Database</p>
                </div>

                <form className="login-form" onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="email">Email Address</label>
                        <input
                            id="email"
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="Enter email address"
                            required
                            autoFocus
                        />
                    </div>

                    <div className="form-group">
                        <label htmlFor="password">Password</label>
                        <input
                            id="password"
                            type="password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            placeholder="Enter password"
                            required
                        />
                    </div>

                    {authError && <div className="login-error">{authError}</div>}

                    <button
                        type="submit"
                        className="login-button"
                        disabled={isSubmitting}
                    >
                        {isSubmitting ? (
                            <Loader2 className="spinner" size={20} />
                        ) : (
                            <>
                                <LogIn size={20} />
                                <span>Login</span>
                            </>
                        )}
                    </button>
                </form>

                <div className="login-footer">
                    <p>Protected resource. Verification required.</p>
                </div>
            </div>
        </div >
    );
};

export default Login;
