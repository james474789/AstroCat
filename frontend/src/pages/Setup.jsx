import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { UserPlus, Orbit } from 'lucide-react';
import { Button } from '../components/ui';
import './Login.css'; // Reuse Login styles

const Setup = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState(null);
    const { registerAdmin } = useAuth();
    const navigate = useNavigate();

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError(null);

        if (password !== confirmPassword) {
            setError("Passwords do not match");
            return;
        }

        setIsSubmitting(true);
        try {
            await registerAdmin(email, password);
            navigate('/');
        } catch (err) {
            setError(err.message || 'Failed to create administrator account');
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <div className="login-container page-login">
            <div className="login-card">
                <div className="login-header">
                    <div className="logo-icon"><Orbit size={48} strokeWidth={1.5} /></div>
                    <h1>Welcome to {import.meta.env.VITE_LOGO_TITLE || 'AstroCat'}</h1>
                    <p>Create your admin account to get started</p>
                </div>

                <form className="login-form" onSubmit={handleSubmit}>
                    <div className="form-group">
                        <label htmlFor="email">Admin Email Address</label>
                        <input
                            id="email"
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            placeholder="Enter admin email"
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
                            placeholder="Create password"
                            required
                        />
                    </div>

                    <div className="form-group">
                        <label htmlFor="confirmPassword">Confirm Password</label>
                        <input
                            id="confirmPassword"
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => setConfirmPassword(e.target.value)}
                            placeholder="Confirm password"
                            required
                        />
                    </div>

                    {error && <div className="login-error">{error}</div>}

                    <Button
                        type="submit"
                        variant="filled"
                        className="login-submit"
                        icon={<UserPlus size={20} />}
                        loading={isSubmitting}
                    >
                        Create Admin Account
                    </Button>
                </form>

                <div className="login-footer">
                    <p>This will be the primary administrator account.</p>
                </div>
            </div>
        </div>
    );
};

export default Setup;
