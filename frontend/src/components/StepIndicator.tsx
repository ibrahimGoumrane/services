import React from 'react';
import { Check } from 'lucide-react';
import { motion } from 'framer-motion';
interface StepIndicatorProps {
  currentStep: number;
}
const STEPS = [
{
  id: 1,
  name: 'Upload'
},
{
  id: 2,
  name: 'Map Fields'
},
{
  id: 3,
  name: 'Process'
},
{
  id: 4,
  name: 'Complete'
}];

export function StepIndicator({ currentStep }: StepIndicatorProps) {
  return (
    <div className="w-full py-4">
      <div className="flex items-center justify-between relative max-w-3xl mx-auto">
        {/* Connecting lines background */}
        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-full h-[3px] bg-slate-800/50 rounded-full z-0" />

        {/* Animated active line */}
        <motion.div
          className="absolute left-0 top-1/2 -translate-y-1/2 h-[3px] bg-gradient-to-r from-blue-500 to-violet-500 rounded-full z-0 shadow-[0_0_10px_rgba(139,92,246,0.5)]"
          initial={{
            width: '0%'
          }}
          animate={{
            width: `${(currentStep - 1) / (STEPS.length - 1) * 100}%`
          }}
          transition={{
            duration: 0.6,
            ease: 'easeInOut'
          }} />
        

        {STEPS.map((step) => {
          const isCompleted = currentStep > step.id;
          const isActive = currentStep === step.id;
          const isUpcoming = currentStep < step.id;
          return (
            <div
              key={step.id}
              className="relative z-10 flex flex-col items-center gap-3 bg-slate-950 px-4">
              
              <motion.div
                initial={false}
                animate={{
                  scale: isActive ? 1.1 : 1,
                  backgroundColor: isCompleted ?
                  '#8b5cf6' :
                  isActive ?
                  '#1e293b' :
                  '#0f172a',
                  borderColor: isCompleted ?
                  '#8b5cf6' :
                  isActive ?
                  '#8b5cf6' :
                  '#334155'
                }}
                transition={{
                  duration: 0.3
                }}
                className={`w-10 h-10 rounded-full flex items-center justify-center text-sm font-semibold border-2 shadow-sm
                  ${isCompleted ? 'text-white shadow-violet-500/30' : ''}
                  ${isActive ? 'text-violet-400 shadow-violet-500/20' : ''}
                  ${isUpcoming ? 'text-slate-500' : ''}
                `}>
                
                {isCompleted ?
                <motion.div
                  initial={{
                    scale: 0
                  }}
                  animate={{
                    scale: 1
                  }}
                  transition={{
                    type: 'spring',
                    stiffness: 300,
                    damping: 20
                  }}>
                  
                    <Check className="w-5 h-5 text-white" />
                  </motion.div> :

                step.id
                }
              </motion.div>
              <span
                className={`text-xs font-medium absolute -bottom-7 whitespace-nowrap transition-colors duration-300
                  ${isActive ? 'text-slate-200' : isCompleted ? 'text-slate-400' : 'text-slate-600'}
                `}>
                
                {step.name}
              </span>
            </div>);

        })}
      </div>
    </div>);

}